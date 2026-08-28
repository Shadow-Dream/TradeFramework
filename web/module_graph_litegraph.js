(function () {
  "use strict";

  const LiteGraph = window.LiteGraph;
  const forms = window.TradeModuleForms;
  const FILTER = "trade-module-graph";
  const INPUT_TYPE = "trade-graph/boundary-input";
  const OUTPUT_TYPE = "trade-graph/boundary-output";
  const UNRESOLVED_TYPE_PREFIX = "trade-module/unresolved/";
  const positionPrefix = "trade.module-graph.positions.v2:";
  const viewportPrefix = "trade.module-graph.viewport.v2:";

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function humanize(value) {
    return forms?.humanizeName ? forms.humanizeName(value) : String(value || "");
  }

  const RESOURCE_ID_LABELS = Object.freeze({
    ds: "Dataset",
    mod: "Module",
    pipe: "Pipeline",
    ws: "Workspace",
    script: "Script",
    sampler: "Sampler",
    env: "Environment",
    bt: "Backtest",
    job: "Backtest job",
    viz: "Visualization",
  });
  const RESOURCE_ID_SOURCE = "(?:ds|mod|pipe|ws|script|sampler|env|bt|job|viz)_[0-9A-Z]{26}";
  const RESOURCE_ID_PATTERN = new RegExp(`^(${RESOURCE_ID_SOURCE})$`, "i");
  const RESOURCE_ID_EMBEDDED_PATTERN = new RegExp(`\\b(${RESOURCE_ID_SOURCE})\\b`, "gi");
  const RESOURCE_COMPOSITE_PATTERN = new RegExp(`^(${RESOURCE_ID_SOURCE})(?:@(?:sha256:)?[a-f0-9]{64}|-current)?$`, "i");
  const RESOURCE_COMPOSITE_EMBEDDED_PATTERN = new RegExp(`\\b(${RESOURCE_ID_SOURCE})(?:@(?:sha256:)?[a-f0-9]{64}|-current)\\b`, "gi");
  const ULID_SOURCE = "[0-9A-HJKMNP-TV-Z]{26}";
  const ULID_PATTERN = new RegExp(`^${ULID_SOURCE}$`, "i");
  const ULID_EMBEDDED_PATTERN = new RegExp(`\\b${ULID_SOURCE}\\b`, "gi");
  const DIGEST_SOURCE = "(?:sha256:)?[a-f0-9]{64}";
  const DIGEST_PATTERN = new RegExp(`^${DIGEST_SOURCE}$`, "i");
  const DIGEST_EMBEDDED_PATTERN = new RegExp(`\\b${DIGEST_SOURCE}\\b`, "gi");
  const UUID_PATTERN = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
  const UUID_EMBEDDED_PATTERN = /\b[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\b/gi;
  const RANDOM_INSTANCE_PATTERN = /^(?:inst[_-]|(?:module|node|input|output|instance)[_-])[a-f0-9]{16,}$/i;
  const RANDOM_INSTANCE_EMBEDDED_PATTERN = /\b(?:inst[_-]|(?:module|node|input|output|instance)[_-])[a-f0-9]{16,}\b/gi;
  const SNAPSHOT_ID_PATTERN = /^(?=.*snapshot)(?:[a-z0-9]+[-_.:]*){2,}(?:[a-f0-9]{16,}|[0-9a-z]{24,})$/i;
  const SNAPSHOT_ID_EMBEDDED_PATTERN = /\b(?:[a-z0-9]+[-_.:]*)*snapshot(?:[-_.:][a-z0-9]+)*(?:[-_.:](?:[a-f0-9]{16,}|[0-9a-z]{24,}))\b/gi;
  const FOLDER_ID_PATTERN = /^folder[_-](?:[0-9a-hjkmnp-tv-z]{26}|[a-f0-9]{16,})$/i;
  const FOLDER_ID_EMBEDDED_PATTERN = /\bfolder[_-](?:[0-9a-hjkmnp-tv-z]{26}|[a-f0-9]{16,})\b/gi;
  const CONTENT_ADDRESSED_ID_PATTERN = /^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+[-_.][a-f0-9]{24,}$/i;
  const CONTENT_ADDRESSED_ID_EMBEDDED_PATTERN = /\b[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+[-_.][a-f0-9]{24,}\b/gi;
  const RANDOM_VISUALIZER_ID_PATTERN = /^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\.(?=[a-z0-9]{8,12}$)(?=[a-z0-9]*\d)[a-z0-9]+$/i;
  const RANDOM_VISUALIZER_ID_EMBEDDED_PATTERN = /\b[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\.(?=[a-z0-9]{8,12}\b)(?=[a-z0-9]*\d)[a-z0-9]+\b/gi;

  function resourceIdentityLabel(value) {
    const match = String(value || "").match(RESOURCE_ID_PATTERN);
    if (!match) return "";
    return RESOURCE_ID_LABELS[match[1].split("_")[0].toLowerCase()] || "Resource";
  }

  function localOpaqueMachineIdentityKind(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    const composite = text.match(RESOURCE_COMPOSITE_PATTERN);
    if (composite) return `${resourceIdentityLabel(composite[1]) || "Resource"}${text.includes("@") ? " version" : ""}`;
    const resourceLabel = resourceIdentityLabel(text);
    if (resourceLabel) return resourceLabel;
    if (DIGEST_PATTERN.test(text)) return "Content fingerprint";
    if (UUID_PATTERN.test(text) || RANDOM_INSTANCE_PATTERN.test(text)) return "Resource instance";
    if (FOLDER_ID_PATTERN.test(text)) return "Folder";
    if (SNAPSHOT_ID_PATTERN.test(text)) return "Snapshot";
    if (CONTENT_ADDRESSED_ID_PATTERN.test(text)) return "Resource snapshot";
    if (RANDOM_VISUALIZER_ID_PATTERN.test(text)) return "Visualizer instance";
    if (ULID_PATTERN.test(text)) return "Resource identity";
    return "";
  }

  function localUserFacingText(value, fallback = "") {
    const original = String(value ?? "");
    const exactKind = localOpaqueMachineIdentityKind(original);
    if (exactKind) return exactKind;
    const redacted = original
      .replace(RESOURCE_COMPOSITE_EMBEDDED_PATTERN, (identity, resourceId) => (
        `${resourceIdentityLabel(resourceId) || "Resource"}${identity.includes("@") ? " version" : ""}`
      ))
      .replace(RESOURCE_ID_EMBEDDED_PATTERN, (identity) => resourceIdentityLabel(identity) || "Resource")
      .replace(DIGEST_EMBEDDED_PATTERN, "content fingerprint")
      .replace(UUID_EMBEDDED_PATTERN, "resource instance")
      .replace(RANDOM_INSTANCE_EMBEDDED_PATTERN, "resource instance")
      .replace(FOLDER_ID_EMBEDDED_PATTERN, "folder")
      .replace(SNAPSHOT_ID_EMBEDDED_PATTERN, "snapshot")
      .replace(CONTENT_ADDRESSED_ID_EMBEDDED_PATTERN, "resource snapshot")
      .replace(RANDOM_VISUALIZER_ID_EMBEDDED_PATTERN, "visualizer instance")
      .replace(ULID_EMBEDDED_PATTERN, "resource identity");
    return redacted.trim() || String(fallback || "");
  }

  function userFacingText(value, fallback = "") {
    const rendered = forms?.userFacingText
      ? forms.userFacingText(value, fallback)
      : value;
    return localUserFacingText(rendered, fallback);
  }

  function isOpaqueMachineIdentity(value) {
    if (forms?.opaqueMachineIdentityKind) return Boolean(forms.opaqueMachineIdentityKind(value));
    return Boolean(localOpaqueMachineIdentityKind(value));
  }

  function moduleKindLabel(module) {
    const kind = humanize(module?.kind || "").trim();
    return kind && !/^module$/i.test(kind) ? `${kind} Module` : "Module";
  }

  function withoutModuleIdentity(value, module, fallback = "Module") {
    let text = userFacingText(value, fallback);
    const identity = String(module?.moduleId || "");
    if (identity.length >= 3) text = text.split(identity).join(moduleKindLabel(module));
    return userFacingText(text, fallback);
  }

  function modulePresentationMeta(module) {
    const category = moduleKindLabel(module);
    const status = userFacingText(humanize(module?.status || ""));
    const version = userFacingText(module?.version);
    const details = [];
    if (version && !isOpaqueMachineIdentity(module?.version)) details.push(`v${version}`);
    if (status) details.push(status);
    return `${category} · ${details.join(" · ") || "Available"}`;
  }

  function uniqueStrings(values = []) {
    return [...new Set(values.filter(Boolean))];
  }

  function schemaToken(schema) {
    return JSON.stringify(window.TradeChartCore?.normalizeSchema?.(schema || {}) || schema || {});
  }

  function schemaFromToken(value) {
    if (value && typeof value === "object") return value;
    if (typeof value !== "string") return {};
    try { return JSON.parse(value); } catch { return {}; }
  }

  function schemasCompatible(outputSchema, inputSchema) {
    const wildcard = (value) => {
      if (value == null || value === 0 || value === "" || value === "*") return true;
      const schema = schemaFromToken(value);
      return !schema || typeof schema !== "object" || Object.keys(schema).length === 0;
    };
    if (wildcard(outputSchema) || wildcard(inputSchema)) return true;
    if (!window.TradeChartCore?.schemasCompatible) return true;
    return window.TradeChartCore.schemasCompatible(
      schemaFromToken(outputSchema),
      schemaFromToken(inputSchema),
    );
  }

  function moduleDisplayName(module) {
    const fallback = modulePresentationMeta(module);
    if (forms?.resourceDisplayName) {
      const fromForms = withoutModuleIdentity(forms.resourceDisplayName({
        ...(module || {}),
        moduleId: undefined,
        instanceId: undefined,
      }, fallback), module, fallback);
      return fromForms === String(module?.moduleId || "") ? fallback : fromForms;
    }
    const candidate = [module?.displayName, module?.name, module?.label]
      .map((value) => String(value || "").trim())
      .find((value) => (
        value
        && value !== String(module?.moduleId || "")
        && !isOpaqueMachineIdentity(value)
      ));
    return candidate ? withoutModuleIdentity(candidate, module, fallback) : fallback;
  }

  function modulePalette(module) {
    const palettes = [
      { color: "#d7f3ef", bgcolor: "#eefbf7", boxcolor: "#0f766e" },
      { color: "#e0ecff", bgcolor: "#f2f7ff", boxcolor: "#1d4ed8" },
      { color: "#ffe8d4", bgcolor: "#fff6ed", boxcolor: "#c2410c" },
      { color: "#e8f2dd", bgcolor: "#f7fbf2", boxcolor: "#4d7c0f" },
    ];
    const repositoryPath = String(module?.folderPath || `/${module?.kind || "Module"}`);
    const hash = [...repositoryPath].reduce(
      (value, character) => ((value * 31) + character.charCodeAt(0)) >>> 0,
      0,
    );
    return palettes[hash % palettes.length];
  }

  function schemaValueType(spec = {}) {
    const raw = Array.isArray(spec.type)
      ? spec.type.find((value) => value && value !== "null") || spec.type[0]
      : spec.type;
    if (spec.enum?.length) return "enum";
    if (raw) return raw;
    if (spec.properties) return "object";
    if (spec.items) return "array";
    return "string";
  }

  function widgetTypeForSpec(spec = {}) {
    const type = schemaValueType(spec);
    if (type === "enum") return "combo";
    if (type === "boolean") return "toggle";
    if (type === "integer" || type === "number") return "number";
    if (type === "string") return "text";
    return "";
  }

  function defaultConfigValue(spec = {}) {
    if (Object.prototype.hasOwnProperty.call(spec, "default")) return clone(spec.default);
    if (spec.enum?.length) return spec.enum[0];
    const type = schemaValueType(spec);
    if (type === "boolean") return false;
    if (type === "integer" || type === "number") return 0;
    if (type === "array") return [];
    if (type === "object") return {};
    return "";
  }

  function normalizedSearchText(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .replace(/\s+/g, " ");
  }

  function moduleSearchScore(module, rawQuery) {
    const query = normalizedSearchText(rawQuery);
    if (!query) return 0;
    const primary = [moduleDisplayName(module), module?.moduleId, module?.folderPath]
      .map(normalizedSearchText);
    const secondary = [module?.description, JSON.stringify(module?.ports || {})]
      .map(normalizedSearchText);
    const score = (value, base) => {
      if (!value) return Number.POSITIVE_INFINITY;
      if (value === query) return base;
      if (value.startsWith(query)) return base + 1;
      const index = value.indexOf(query);
      return index < 0 ? Number.POSITIVE_INFINITY : base + 2 + (index / 1000);
    };
    return Math.min(
      ...primary.map((value) => score(value, 0)),
      ...secondary.map((value) => score(value, 10)),
    );
  }

  function opaqueId(prefix) {
    const token = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
      || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${token}`;
  }

  function moduleType(module) {
    return `trade-module/${module.kind}/${module.moduleId}/${module.version}`;
  }

  function moduleKey(module) {
    return `${module.kind}/${module.moduleId}/${module.version}`;
  }

  function moduleLabel(module) {
    const version = userFacingText(module?.version);
    return version && !isOpaqueMachineIdentity(module?.version)
      ? `${moduleDisplayName(module)} · v${version}`
      : moduleDisplayName(module);
  }

  function graphDocument(value) {
    const graph = value && typeof value === "object" ? value : {};
    return {
      nodes: Array.isArray(graph.nodes) ? [...graph.nodes] : [],
      inputs: graph.inputs && typeof graph.inputs === "object" && !Array.isArray(graph.inputs)
        ? clone(graph.inputs) : {},
      outputs: graph.outputs && typeof graph.outputs === "object" && !Array.isArray(graph.outputs)
        ? clone(graph.outputs) : {},
    };
  }

  function inputSourceModel(inputSources, defaultLabel) {
    const namedLabels = inputSources && typeof inputSources === "object" && !Array.isArray(inputSources)
      ? Object.fromEntries(
        Object.entries(inputSources)
          .map(([source, label]) => [String(source || "").trim(), String(label || source).trim()])
          .filter(([source, label]) => source && label),
      )
      : {};
    const namedSources = Object.keys(namedLabels);
    const rawEntries = namedSources.length
      ? [["", String(defaultLabel || "Default input")], ...Object.entries(namedLabels)]
      : [];
    const usedLabels = new Set();
    const entries = rawEntries.map(([source, rawLabel]) => {
      let label = rawLabel;
      let ordinal = 2;
      while (usedLabels.has(label)) {
        label = `${rawLabel} · ${ordinal}`;
        ordinal += 1;
      }
      usedLabels.add(label);
      return [source, label];
    });
    return {
      namedSources,
      labelsBySource: Object.fromEntries(entries),
      sourceByLabel: Object.fromEntries(entries.map(([source, label]) => [label, source])),
      widgetValues: entries.map(([, label]) => label),
      sources: entries.map(([source]) => source),
    };
  }

  function graphInputBoundary(dataKey, wire, source, hasNamedSources) {
    const boundary = { dataKey: String(dataKey || "").trim(), wire: String(wire || "").trim() };
    if (hasNamedSources && source) boundary.source = String(source).trim();
    return boundary;
  }

  function registerBoundaryTypes() {
    if (!LiteGraph.registered_node_types?.[INPUT_TYPE]) {
      class GraphInputNode extends LiteGraph.LGraphNode {
        constructor() {
          super();
          this.title = "Graph Input";
          this.color = "#d7f3ef";
          this.bgcolor = "#eefbf7";
          this.boxcolor = "#0f766e";
          this.properties = { dataKey: "", wire: "" };
          this.addOutput("data", 0);
          this.addWidget("text", "Data key", "", (value) => {
            this.properties.dataKey = String(value || "").trim();
            if (this.outputs?.[0]) {
              this.outputs[0].type = 0;
            }
            this.title = this.properties.dataKey
              ? `Input: ${userFacingText(this.properties.dataKey, "Data key")}`
              : "Graph Input";
            this.graph?._tradeSchedule?.();
          });
          this.addWidget("text", "Wire", "", (value) => {
            this.properties.wire = String(value || "").trim();
            this.graph?._tradeSchedule?.();
          });
          this.size = [340, 126];
        }

        onConnectionsChange() { this.graph?._tradeSchedule?.(); }
      }
      GraphInputNode.title = "Graph Input";
      GraphInputNode.filter = FILTER;
      LiteGraph.registerNodeType(INPUT_TYPE, GraphInputNode);
    }

    if (!LiteGraph.registered_node_types?.[OUTPUT_TYPE]) {
      class GraphOutputNode extends LiteGraph.LGraphNode {
        constructor() {
          super();
          this.title = "Graph Output";
          this.color = "#ffe8d4";
          this.bgcolor = "#fff6ed";
          this.boxcolor = "#c2410c";
          this.properties = { dataKey: "" };
          this.addInput("data", 0);
          this.addWidget("text", "Data key", "", (value) => {
            this.properties.dataKey = String(value || "").trim();
            this.title = this.properties.dataKey
              ? `Output: ${userFacingText(this.properties.dataKey, "Data key")}`
              : "Graph Output";
            this.graph?._tradeSchedule?.();
          });
          this.size = [340, 108];
        }

        onConnectionsChange() { this.graph?._tradeSchedule?.(); }
      }
      GraphOutputNode.title = "Graph Output";
      GraphOutputNode.filter = FILTER;
      LiteGraph.registerNodeType(OUTPUT_TYPE, GraphOutputNode);
    }
  }

  function registerModuleTypes(modules) {
    modules.forEach((module) => {
      const type = moduleType(module);
      if (LiteGraph.registered_node_types?.[type]) return;
      class ModuleNode extends LiteGraph.LGraphNode {
        constructor() {
          super();
          const palette = modulePalette(module);
          this.title = moduleDisplayName(module);
          this.size = [360, 150];
          this.color = palette.color;
          this.bgcolor = palette.bgcolor;
          this.boxcolor = palette.boxcolor;
          this.properties = {};
          Object.entries(module.ports?.inputs || {}).forEach(([name, spec]) => {
            this.addInput(name, schemaToken(spec?.schema));
          });
          Object.entries(module.ports?.outputs || {}).forEach(([name, spec]) => {
            this.addOutput(name, schemaToken(spec?.schema));
          });
          Object.entries(module.configSchema?.properties || {}).forEach(([name, spec]) => {
            const widgetType = widgetTypeForSpec(spec);
            this.properties[name] = defaultConfigValue(spec);
            if (!widgetType) return;
            const widget = this.addWidget(
              widgetType,
              humanize(name),
              this.properties[name],
              (value) => {
                this.properties[name] = value;
                this.graph?._tradeSchedule?.();
              },
              widgetType === "combo" ? { values: spec.enum || [] } : {},
            );
            widget._tradeConfigKey = name;
          });
          const computed = this.computeSize();
          this.size = [Math.max(360, computed[0]), Math.max(130, computed[1] + 10)];
        }

        onConnectInput(inputIndex, outputType) {
          const name = Object.keys(module.ports?.inputs || {})[inputIndex];
          return schemasCompatible(outputType, module.ports?.inputs?.[name]?.schema || {});
        }

        onConnectionsChange() { this.graph?._tradeSchedule?.(); }
      }
      ModuleNode.title = moduleDisplayName(module);
      ModuleNode.desc = withoutModuleIdentity(
        module.description,
        module,
        `${moduleKindLabel(module)} definition`,
      );
      ModuleNode.filter = FILTER;
      LiteGraph.registerNodeType(type, ModuleNode);
    });
  }

  function inferredModuleDefinition(instance) {
    return {
      kind: instance.kind,
      moduleId: instance.moduleId,
      version: instance.version,
      name: `${moduleKindLabel(instance)} · Unavailable revision`,
      description: "The exact locked Module revision is not present in the current catalog.",
      ports: {
        inputs: Object.fromEntries(Object.keys(instance.inputs || {}).map((name) => [name, {}])),
        outputs: Object.fromEntries(Object.keys(instance.outputs || {}).map((name) => [name, {}])),
      },
      unresolved: true,
    };
  }

  function registerUnresolvedModuleType(instance, definition) {
    const type = `${UNRESOLVED_TYPE_PREFIX}${instance.kind}/${instance.moduleId}/${instance.version}`;
    if (!LiteGraph.registered_node_types?.[type]) {
      class UnresolvedModuleNode extends LiteGraph.LGraphNode {
        constructor() {
          super();
          this.title = moduleDisplayName(definition);
          this.size = [360, 150];
          this.color = "#f8e4b7";
          this.bgcolor = "#fff8e8";
          this.boxcolor = "#b7791f";
          Object.keys(definition.ports.inputs).forEach((name) => this.addInput(name, 0));
          Object.keys(definition.ports.outputs).forEach((name) => this.addOutput(name, 0));
        }

        onConnectionsChange() { this.graph?._tradeSchedule?.(); }
      }
      UnresolvedModuleNode.title = moduleDisplayName(definition);
      UnresolvedModuleNode.desc = userFacingText(definition.description, "Module revision unavailable");
      UnresolvedModuleNode.filter = FILTER;
      LiteGraph.registerNodeType(type, UnresolvedModuleNode);
    }
    return type;
  }

  function mount(options = {}) {
    const {
      root,
      modules = [],
      moduleChoices = null,
      instances = {},
      alphaGraph = {},
      meta = {},
      versions = [],
      loadedVersion = "",
      onChange,
      moduleKind = "Signal",
      graphLabel = "Module Graph",
      contextLabel = "Resource",
      backLabel = "Back",
      storageNamespace = "graph",
      inputSources = null,
      defaultInputSourceLabel = "Default input",
      resourceEditor = null,
      actions = {},
    } = options;
    if (!root || !LiteGraph?.LGraph || !LiteGraph?.LGraphCanvas) return null;
    root.__moduleGraphCleanup?.();

    LiteGraph.search_filter_enabled = true;
    LiteGraph.auto_load_slot_types = true;
    LiteGraph.middle_click_slot_add_default_node = false;
    LiteGraph.release_link_on_empty_shows_menu = false;

    const definitionModules = modules
      .filter((module) => module.kind === moduleKind && module.status === "archived")
      .sort((left, right) => moduleLabel(left).localeCompare(moduleLabel(right)));
    const availableModules = (Array.isArray(moduleChoices)
      ? moduleChoices
      : (window.TradeVersionSelection?.currentRows(
          definitionModules,
          ["kind", "moduleId"],
        ) || definitionModules))
      .filter((module) => module.kind === moduleKind && module.status === "archived")
      .sort((left, right) => moduleLabel(left).localeCompare(moduleLabel(right)));
    const definitionByKey = new Map(definitionModules.map((module) => [moduleKey(module), module]));
    const definitionForInstance = (instance) => definitionByKey.get(
      `${instance?.kind}/${instance?.moduleId}/${instance?.version}`,
    );
    const contextId = String(meta.contextId || meta.pipelineId || "graph");
    const positionKey = `${positionPrefix}${storageNamespace}:${contextId}`;
    const viewportKey = `${viewportPrefix}${storageNamespace}:${contextId}`;
    const graph = new LiteGraph.LGraph();
    graph.filter = FILTER;
    let emitTimer = 0;
    let suppress = true;
    let dirty = false;
    let destroyed = false;
    let initialized = false;
    let shouldAutoLayout = false;
    let currentInstances = clone(instances || {});
    let currentDocument = graphDocument(alphaGraph);
    const nodeMeta = new Map();
    let selectedInspectorNodeId = null;
    let inspectorExplorerQuery = "";
    let inspectorExplorerMode = "all";
    let inspectorExplorerActiveIndex = 0;
    let activePortPicker = null;
    let pendingModuleConnection = null;
    let moduleChooserResults = [];
    let moduleChooserActiveIndex = 0;
    let graphFullscreen = false;
    let explorerCollapsed = false;
    let clipboard = null;
    let clipboardPasteCount = 0;
    let currentValidation = { errors: [], warnings: [] };
    let historyApplying = false;
    let historyIndex = -1;
    let savedDraftSignature = "";
    const historyEntries = [];
    const HISTORY_LIMIT = 60;
    let validationSequence = 0;
    let authorityValidation = actions.onValidate
      ? { state: "pending", valid: false, message: "Validating with Engine…" }
      : { state: "unavailable", valid: false, message: "Engine validation is unavailable." };
    let authorityPromise = Promise.resolve(authorityValidation);
    const resourceFields = Array.isArray(resourceEditor?.fields)
      ? resourceEditor.fields.filter((field) => field?.key && field?.label)
      : [];
    const resourceValues = Object.fromEntries(resourceFields.map((field) => [field.key, String(field.value ?? "")]));
    const sourceModel = inputSourceModel(inputSources, defaultInputSourceLabel);
    const namedInputSourceNames = sourceModel.namedSources;
    const inputSourceLabels = sourceModel.labelsBySource;
    const inputSourceByLabel = sourceModel.sourceByLabel;
    const inputSourceWidgetValues = sourceModel.widgetValues;
    const inputSourceNames = sourceModel.sources;

    function graphFacingText(value, fallback = "") {
      let text = userFacingText(value, fallback);
      const replacements = [];
      definitionModules.forEach((definition) => {
        replacements.push([definition.moduleId, moduleKindLabel(definition)]);
      });
      Object.entries(currentInstances || {}).forEach(([instanceId, instance]) => {
        replacements.push([instanceId, "Module instance"]);
        replacements.push([instance?.moduleId, moduleKindLabel(instance)]);
      });
      replacements.push([meta.contextId, humanize(contextLabel || "Resource")]);
      replacements.push([meta.pipelineId, "Pipeline"]);
      replacements
        .filter(([identity]) => String(identity || "").length >= 3)
        .sort((left, right) => String(right[0]).length - String(left[0]).length)
        .forEach(([identity, label]) => {
          text = text.split(String(identity)).join(String(label || "Resource"));
        });
      return userFacingText(text, fallback);
    }

    function isGraphMachineIdentity(value) {
      const raw = String(value ?? "");
      return isOpaqueMachineIdentity(raw) || graphFacingText(raw) !== userFacingText(raw);
    }

    function contextName() {
      const editedName = resourceEditor?.contextName?.(clone(resourceValues));
      return graphFacingText(
        editedName || meta.name,
        `${humanize(contextLabel || "Resource")} · Draft`,
      );
    }

    function scheduleEmit() {
      if (suppress || destroyed) return;
      dirty = true;
      syncStatus();
      clearTimeout(emitTimer);
      emitTimer = window.setTimeout(emit, 80);
    }

    graph._tradeSchedule = scheduleEmit;
    registerBoundaryTypes();
    registerModuleTypes(definitionModules);
    const previousValidConnection = LiteGraph.isValidConnection;
    const tradeValidConnection = (typeA, typeB) => {
      const isSchemaSlot = (value) => (
        value === 0
        || value === ""
        || value === "*"
        || (typeof value === "string" && /^[{[]/.test(value.trim()))
      );
      if (isSchemaSlot(typeA) || isSchemaSlot(typeB)) {
        return schemasCompatible(typeA, typeB);
      }
      return previousValidConnection.call(LiteGraph, typeA, typeB);
    };
    LiteGraph.isValidConnection = tradeValidConnection;

    const versionRows = Array.isArray(versions) ? [...versions].reverse() : [];
    root.innerHTML = `
      <div class="alpha-litegraph-shell">
        <div class="alpha-litegraph-toolbar">
          <div class="alpha-litegraph-toolbar-row alpha-litegraph-toolbar-row-primary">
            <div class="alpha-litegraph-context"><span>${escapeHtml(graphFacingText(contextLabel, "Resource"))}</span><strong data-graph-context-name>${escapeHtml(contextName())}</strong></div>
            <div class="alpha-litegraph-toolbar-actions alpha-litegraph-layout-actions">
              <div class="alpha-litegraph-view-tools" role="group" aria-label="Graph view controls">
                <button type="button" data-graph-arrange>Arrange</button>
                <button type="button" data-graph-fit>Fit</button>
                <button type="button" data-graph-fullscreen aria-pressed="false">Fullscreen</button>
                <button type="button" data-graph-toggle-explorer aria-pressed="false">Hide Sidebar</button>
              </div>
              <button class="alpha-litegraph-back-action" type="button" data-graph-back>${escapeHtml(backLabel)}</button>
            </div>
          </div>
          <div class="alpha-litegraph-toolbar-row alpha-litegraph-toolbar-row-secondary">
            <div class="alpha-litegraph-toolbar-actions alpha-litegraph-history-actions">
              <button type="button" data-graph-undo disabled>Undo</button>
              <button type="button" data-graph-redo disabled>Redo</button>
            </div>
            <div class="alpha-litegraph-toolbar-actions alpha-litegraph-version-actions">
              <label class="alpha-litegraph-picker alpha-litegraph-revision-picker" data-graph-version-wrap><span>Revision</span><select data-graph-version>
                ${versionRows.map((row) => `<option value="${escapeHtml(row.version)}" ${String(row.version) === String(loadedVersion) ? "selected" : ""}>${isOpaqueMachineIdentity(row.version) ? "Revision" : `v${escapeHtml(userFacingText(row.version, "Revision"))}`}</option>`).join("")}
              </select></label>
              <button type="button" data-graph-load>Load Revision</button>
              <button class="alpha-litegraph-save-action" type="button" data-graph-save>Save Revision</button>
            </div>
          </div>
        </div>
        <div class="alpha-litegraph-body">
          <div class="alpha-litegraph-stage"><canvas class="alpha-litegraph-canvas"></canvas></div>
          <aside class="alpha-litegraph-inspector">
            <div class="alpha-litegraph-explorer-head">
              <div><strong>Node Explorer</strong><span>Browse and add repository Modules</span></div>
              <div class="alpha-litegraph-node-add">
                <div class="alpha-module-combobox">
                  <input type="search" data-graph-module autocomplete="off" placeholder="Search modules to add" aria-label="Search current ${escapeHtml(moduleKind)} Modules" aria-autocomplete="list" aria-expanded="false" />
                  <div class="alpha-module-combobox-menu" data-graph-module-options role="listbox" hidden></div>
                </div>
                <button type="button" data-graph-add-module>Add</button>
              </div>
              <div class="alpha-litegraph-boundary-actions" role="group" aria-label="Graph boundaries and resource details">
                <button type="button" data-graph-input>Add Input</button>
                <button type="button" data-graph-output>Add Output</button>
                ${resourceFields.length ? '<button type="button" data-graph-rename>Edit Resource</button>' : ""}
              </div>
            </div>
            <div class="alpha-litegraph-inspector-content" data-graph-inspector></div>
          </aside>
        </div>
        <div class="alpha-litegraph-footer">
          <span class="alpha-litegraph-shortcuts muted">Ctrl/Cmd+K focuses Add Node search. Ctrl/Cmd+S saves. Ctrl/Cmd+C/X/V, Ctrl/Cmd+A/D, and Backspace/Delete edit the graph.</span>
          <div class="alpha-litegraph-validation" data-graph-validation></div>
          <span class="alpha-litegraph-status" data-graph-status data-state="saved">Saved</span>
        </div>
        <div class="alpha-litegraph-error-toast" data-graph-error-toast role="alert" aria-live="assertive" hidden>
          <div class="alpha-litegraph-error-toast-copy"><strong>Error</strong><span data-graph-error-message></span></div>
          <button type="button" data-graph-error-dismiss aria-label="Dismiss error">×</button>
        </div>
      </div>`;

    const canvasElement = root.querySelector("canvas");
    const stageElement = root.querySelector(".alpha-litegraph-stage");
    const statusElement = root.querySelector("[data-graph-status]");
    const validationElement = root.querySelector("[data-graph-validation]");
    const inspectorElement = root.querySelector("[data-graph-inspector]");
    const moduleSelect = root.querySelector("[data-graph-module]");
    const moduleOptionsElement = root.querySelector("[data-graph-module-options]");
    const addModuleButton = root.querySelector("[data-graph-add-module]");
    const errorToastElement = root.querySelector("[data-graph-error-toast]");
    const errorMessageElement = root.querySelector("[data-graph-error-message]");
    const saveAction = actions.onSave || window.__tradePipelineActions?.saveCurrentPipelineVersion;
    const loadAction = actions.onLoad || window.__tradePipelineActions?.loadPipelineVersion;
    root.querySelector("[data-graph-save]").hidden = !saveAction;
    root.querySelector("[data-graph-load]").hidden = !loadAction;
    root.querySelector("[data-graph-version-wrap]").hidden = !loadAction;

    function selectedChooserModule() {
      const key = moduleSelect?.dataset.moduleKey || "";
      return key ? definitionByKey.get(key) || null : null;
    }

    function moduleMatchesPendingConnection(module) {
      if (!pendingModuleConnection) return true;
      if (pendingModuleConnection.sourceNodeId != null) {
        const source = graph.getNodeById(pendingModuleConnection.sourceNodeId);
        const sourceType = source?.outputs?.[pendingModuleConnection.sourceSlot]?.type || 0;
        return Object.values(module.ports?.inputs || {}).some((spec) => (
          schemasCompatible(sourceType, spec?.schema || {})
        ));
      }
      const target = graph.getNodeById(pendingModuleConnection.targetNodeId);
      const targetType = target?.inputs?.[pendingModuleConnection.targetSlot]?.type || 0;
      return Object.values(module.ports?.outputs || {}).some((spec) => (
        schemasCompatible(spec?.schema || {}, targetType)
      ));
    }

    function moduleConnectionPrompt() {
      if (!pendingModuleConnection) return "Search modules to add";
      return pendingModuleConnection.sourceNodeId != null
        ? "Search compatible downstream modules"
        : "Search compatible upstream modules";
    }

    function syncModuleAddButton() {
      addModuleButton.disabled = !selectedChooserModule();
    }

    function renderModuleChooser(query = "") {
      const normalized = String(query || "").trim();
      moduleChooserResults = availableModules
        .filter(moduleMatchesPendingConnection)
        .map((module) => ({ module, score: moduleSearchScore(module, normalized) }))
        .filter((entry) => !normalized || Number.isFinite(entry.score))
        .sort((left, right) => (
          left.score - right.score
          || moduleDisplayName(left.module).localeCompare(moduleDisplayName(right.module))
        ))
        .slice(0, 80)
        .map((entry) => entry.module);
      moduleChooserActiveIndex = Math.max(
        0,
        Math.min(moduleChooserActiveIndex, Math.max(0, moduleChooserResults.length - 1)),
      );
      const grouped = new Map();
      moduleChooserResults.forEach((module) => {
        const folder = String(module.folderPath || `/${moduleKind}`);
        if (!grouped.has(folder)) grouped.set(folder, []);
        grouped.get(folder).push(module);
      });
      moduleOptionsElement.innerHTML = moduleChooserResults.length
        ? [...grouped.entries()].map(([folder, rows]) => `
          <div class="alpha-module-combobox-group">
            <span>${escapeHtml(graphFacingText(folder, `/${moduleKind}`))}</span>
            ${rows.map((module) => {
              const index = moduleChooserResults.indexOf(module);
              return `<button type="button" role="option" data-graph-module-choice="${escapeHtml(moduleKey(module))}" class="${index === moduleChooserActiveIndex ? "active" : ""}"><strong>${escapeHtml(moduleDisplayName(module))}</strong><small>${escapeHtml(modulePresentationMeta(module))}</small></button>`;
            }).join("")}
          </div>`).join("")
        : `<div class="alpha-module-combobox-empty">No matching ${escapeHtml(moduleKind)} Modules</div>`;
      moduleOptionsElement.hidden = false;
      moduleSelect.setAttribute("aria-expanded", "true");
    }

    function closeModuleChooser() {
      moduleOptionsElement.hidden = true;
      moduleSelect.setAttribute("aria-expanded", "false");
    }

    function chooseModule(module, { close = true } = {}) {
      if (!module) return;
      moduleSelect.value = moduleDisplayName(module);
      moduleSelect.dataset.moduleKey = moduleKey(module);
      if (close) closeModuleChooser();
      syncModuleAddButton();
    }

    function focusModuleChooser(connection = null) {
      pendingModuleConnection = connection;
      if (explorerCollapsed) {
        explorerCollapsed = false;
        applyGraphLayoutState();
      }
      delete moduleSelect.dataset.moduleKey;
      moduleSelect.value = "";
      moduleSelect.placeholder = moduleConnectionPrompt();
      syncModuleAddButton();
      moduleSelect.focus();
      moduleSelect.select();
      moduleChooserActiveIndex = 0;
      renderModuleChooser(moduleSelect.value);
    }

    function showError(error) {
      const message = graphFacingText(
        error?.message || error,
        "Unexpected Graph editor error",
      );
      errorMessageElement.textContent = message;
      errorToastElement.hidden = false;
    }

    function dismissError() {
      errorToastElement.hidden = true;
      errorMessageElement.textContent = "";
    }

    const onModuleChooserDocumentPointerDown = (event) => {
      if (!event.target.closest(".alpha-module-combobox")) closeModuleChooser();
    };
    document.addEventListener("pointerdown", onModuleChooserDocumentPointerDown);
    moduleSelect.addEventListener("focus", () => {
      moduleChooserActiveIndex = 0;
      renderModuleChooser(moduleSelect.value);
    });
    moduleSelect.addEventListener("input", () => {
      delete moduleSelect.dataset.moduleKey;
      moduleChooserActiveIndex = 0;
      renderModuleChooser(moduleSelect.value);
      syncModuleAddButton();
    });
    moduleSelect.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        pendingModuleConnection = null;
        moduleSelect.placeholder = moduleConnectionPrompt();
        closeModuleChooser();
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        const count = moduleChooserResults.length;
        if (count) moduleChooserActiveIndex = (moduleChooserActiveIndex + direction + count) % count;
        renderModuleChooser(moduleSelect.value);
        moduleOptionsElement.querySelector(".active")?.scrollIntoView({ block: "nearest" });
        return;
      }
      if (event.key === "Enter" && moduleChooserResults.length) {
        event.preventDefault();
        chooseModule(moduleChooserResults[moduleChooserActiveIndex] || moduleChooserResults[0]);
      }
    });
    moduleOptionsElement.addEventListener("pointerdown", (event) => event.preventDefault());
    moduleOptionsElement.addEventListener("click", (event) => {
      const key = event.target.closest("[data-graph-module-choice]")?.dataset.graphModuleChoice;
      if (key) chooseModule(definitionByKey.get(key));
    });
    errorToastElement.querySelector("[data-graph-error-dismiss]").addEventListener("click", dismissError);

    const canvas = new LiteGraph.LGraphCanvas(canvasElement, graph, {
      autoresize: false,
      background_image: null,
    });
    canvas.filter = FILTER;
    canvas.allow_searchbox = false;
    canvas.allow_reconnect_links = true;
    canvas.connections_width = 4;
    canvas.render_connections_border = true;
    canvas.getMenuOptions = () => [{
      content: "Add Modules from the Node Explorer",
      disabled: true,
    }];
    canvas.getNodeMenuOptions = (node) => [{
      content: "Remove",
      disabled: node?.removable === false || node?.block_delete,
      callback: LiteGraph.LGraphCanvas.onMenuNodeRemove,
    }];

    function storedPositions() {
      try { return JSON.parse(localStorage.getItem(positionKey) || "{}"); } catch { return {}; }
    }

    function positionFor(id, index) {
      const stored = storedPositions()[id];
      return stored ? [stored.x, stored.y] : [80 + (index % 4) * 390, 80 + Math.floor(index / 4) * 210];
    }

    function savePositions() {
      const result = {};
      graph._nodes.forEach((node) => {
        const id = nodeMeta.get(node.id)?.id;
        if (id) result[id] = { x: Math.round(node.pos[0]), y: Math.round(node.pos[1]) };
      });
      localStorage.setItem(positionKey, JSON.stringify(result));
    }

    function hasCompleteStoredLayout() {
      const stored = storedPositions();
      return Boolean(graph._nodes.length) && graph._nodes.every((node) => {
        const id = nodeMeta.get(node.id)?.id;
        const position = id ? stored[id] : null;
        return Number.isFinite(position?.x) && Number.isFinite(position?.y);
      });
    }

    function layeredLayout({ fit = true } = {}) {
      const nodes = [...graph._nodes];
      if (!nodes.length) return;
      const nodeIds = new Set(nodes.map((node) => node.id));
      const outgoing = new Map(nodes.map((node) => [node.id, []]));
      const indegree = new Map(nodes.map((node) => [node.id, 0]));
      const depth = new Map(nodes.map((node) => [
        node.id,
        nodeMeta.get(node.id)?.entity === "input" ? 0 : 1,
      ]));
      Object.values(graph.links || {}).filter(Boolean).forEach((link) => {
        if (!nodeIds.has(link.origin_id) || !nodeIds.has(link.target_id)) return;
        outgoing.get(link.origin_id).push(link.target_id);
        indegree.set(link.target_id, indegree.get(link.target_id) + 1);
      });
      const queue = nodes.filter((node) => indegree.get(node.id) === 0);
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const source = queue[cursor];
        outgoing.get(source.id).forEach((targetId) => {
          depth.set(targetId, Math.max(depth.get(targetId), depth.get(source.id) + 1));
          indegree.set(targetId, indegree.get(targetId) - 1);
          if (indegree.get(targetId) === 0) queue.push(graph.getNodeById(targetId));
        });
      }
      const moduleDepths = nodes
        .filter((node) => nodeMeta.get(node.id)?.entity === "module")
        .map((node) => depth.get(node.id));
      const outputFloor = Math.max(1, ...(moduleDepths.length ? moduleDepths.map((value) => value + 1) : [1]));
      nodes.forEach((node) => {
        if (nodeMeta.get(node.id)?.entity === "output") {
          depth.set(node.id, Math.max(depth.get(node.id), outputFloor));
        }
      });
      const columns = new Map();
      nodes.forEach((node, order) => {
        const column = depth.get(node.id);
        if (!columns.has(column)) columns.set(column, []);
        columns.get(column).push({ node, order });
      });
      let x = 70;
      [...columns.keys()].sort((left, right) => left - right).forEach((column) => {
        let y = 70;
        let width = 0;
        columns.get(column)
          .sort((left, right) => left.order - right.order)
          .forEach(({ node }) => {
            const size = node.size || node.computeSize?.() || [340, 140];
            node.pos = [x, y];
            width = Math.max(width, Number(size[0]) || 340);
            y += (Number(size[1]) || 140) + 70;
          });
        x += width + 120;
      });
      savePositions();
      graph.setDirtyCanvas(true, true);
      if (fit) {
        fitGraph();
        window.requestAnimationFrame(fitGraph);
      }
    }

    function fitGraph() {
      frameNodes(graph._nodes);
    }

    function frameNodes(nodes = []) {
      const visibleNodes = uniqueStrings(nodes.map((node) => node?.id))
        .map((id) => graph.getNodeById(id))
        .filter(Boolean);
      if (!visibleNodes.length) return;
      const padding = 55;
      const bounds = visibleNodes.reduce((result, node) => {
        const size = node.size || node.computeSize?.() || [340, 140];
        result.left = Math.min(result.left, Number(node.pos[0]) || 0);
        result.top = Math.min(result.top, (Number(node.pos[1]) || 0) - 30);
        result.right = Math.max(result.right, (Number(node.pos[0]) || 0) + (Number(size[0]) || 340));
        result.bottom = Math.max(result.bottom, (Number(node.pos[1]) || 0) + (Number(size[1]) || 140));
        return result;
      }, { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity });
      const graphWidth = Math.max(1, bounds.right - bounds.left);
      const graphHeight = Math.max(1, bounds.bottom - bounds.top);
      const scale = Math.max(
        canvas.ds.min_scale || 0.1,
        Math.min(1, (canvasElement.width - padding * 2) / graphWidth, (canvasElement.height - padding * 2) / graphHeight),
      );
      canvas.ds.scale = scale;
      canvas.ds.offset[0] = (canvasElement.width / scale - graphWidth) / 2 - bounds.left;
      canvas.ds.offset[1] = (canvasElement.height / scale - graphHeight) / 2 - bounds.top;
      graph.setDirtyCanvas(true, true);
    }

    function revealNode(node) {
      if (!node) return;
      const scale = Number(canvas.ds.scale) || 1;
      const offset = canvas.ds.offset || [0, 0];
      const size = node.size || node.computeSize?.() || [340, 140];
      const left = ((Number(node.pos?.[0]) || 0) + offset[0]) * scale;
      const top = ((Number(node.pos?.[1]) || 0) - 30 + offset[1]) * scale;
      const right = left + (Number(size[0]) || 340) * scale;
      const bottom = top + ((Number(size[1]) || 140) + 30) * scale;
      const padding = 28;
      const visible = left >= padding
        && top >= padding
        && right <= stageElement.clientWidth - padding
        && bottom <= stageElement.clientHeight - padding;
      if (!visible) canvas.centerOnNode?.(node);
      graph.setDirtyCanvas(true, true);
    }

    function selectNodes(nodes = []) {
      const selected = nodes.filter((node) => node?.graph === graph);
      if (!selected.length) return;
      canvas.deselectAllNodes?.();
      if (typeof canvas.selectNodes === "function") {
        canvas.selectNodes(selected, false);
      } else {
        selected.forEach((node, index) => canvas.selectNode?.(node, index > 0));
      }
      selectedInspectorNodeId = selected[selected.length - 1].id;
      renderInspector();
      graph.setDirtyCanvas(true, true);
    }

    function arrangeNodeSubset(nodes = []) {
      const subset = nodes.filter((node) => node?.graph === graph);
      if (!subset.length) return;
      const columns = Math.max(1, Math.ceil(Math.sqrt(subset.length)));
      const left = Math.min(...subset.map((node) => Number(node.pos?.[0]) || 0));
      const top = Math.min(...subset.map((node) => Number(node.pos?.[1]) || 0));
      subset.forEach((node, index) => {
        node.pos = [left + (index % columns) * 420, top + Math.floor(index / columns) * 220];
      });
      savePositions();
      recordHistory();
      graph.setDirtyCanvas(true, true);
    }

    function applyGraphLayoutState() {
      root.classList.toggle("alpha-graph-fullscreen", graphFullscreen);
      root.classList.toggle("alpha-explorer-collapsed", explorerCollapsed);
      document.body.classList.toggle("alpha-graph-modal-open", graphFullscreen);
      const fullscreenButton = root.querySelector("[data-graph-fullscreen]");
      const explorerButton = root.querySelector("[data-graph-toggle-explorer]");
      fullscreenButton.textContent = graphFullscreen ? "Exit Fullscreen" : "Fullscreen";
      fullscreenButton.setAttribute("aria-pressed", graphFullscreen ? "true" : "false");
      explorerButton.textContent = explorerCollapsed ? "Show Sidebar" : "Hide Sidebar";
      explorerButton.setAttribute("aria-pressed", explorerCollapsed ? "true" : "false");
      window.requestAnimationFrame(() => {
        resize();
        graph.setDirtyCanvas(true, true);
      });
    }

    function setNodeWidget(node, index, value) {
      if (node.widgets?.[index]) node.widgets[index].value = value;
    }

    function addBoundary(kind, id, binding, index) {
      const input = kind === "input";
      const node = LiteGraph.createNode(input ? INPUT_TYPE : OUTPUT_TYPE);
      if (!node) throw new Error(`Unable to create Graph ${kind} boundary.`);
      node.pos = positionFor(id, index);
      node.properties.dataKey = String(binding?.dataKey || "");
      if (input && node.outputs?.[0]) {
        node.outputs[0].type = 0;
      }
      setNodeWidget(node, 0, graphFacingText(node.properties.dataKey));
      if (input) {
        node.properties.wire = String(binding?.wire || `input.${id}`);
        setNodeWidget(node, 1, graphFacingText(node.properties.wire));
        if (namedInputSourceNames.length) {
          node.properties.source = String(binding?.source || "");
          node.addWidget("combo", "Source", inputSourceLabels[node.properties.source], (value) => {
            node.properties.source = inputSourceByLabel[String(value)] ?? "";
            node.graph?._tradeSchedule?.();
          }, { values: inputSourceWidgetValues });
          node.size = [340, 150];
        }
      }
      node.title = node.properties.dataKey
        ? `${input ? "Input" : "Output"}: ${graphFacingText(node.properties.dataKey, "Data key")}`
        : `Graph ${input ? "Input" : "Output"}`;
      graph.add(node);
      nodeMeta.set(node.id, { entity: kind, id });
      return node;
    }

    function addModule(instance, definition, index) {
      const nodeType = definition.unresolved
        ? registerUnresolvedModuleType(instance, definition)
        : moduleType(definition);
      const node = LiteGraph.createNode(nodeType);
      if (!node) throw new Error(`Unable to create ${moduleDisplayName(definition)} node.`);
      node.pos = positionFor(instance.instanceId, index);
      node.properties = clone(instance.config || {});
      (node.widgets || []).forEach((widget) => {
        const key = widget._tradeConfigKey;
        if (key && Object.prototype.hasOwnProperty.call(node.properties, key)) {
          widget.value = isGraphMachineIdentity(node.properties[key])
            ? "Existing resource selection"
            : node.properties[key];
        }
      });
      graph.add(node);
      nodeMeta.set(node.id, {
        entity: "module",
        id: instance.instanceId,
        definition,
        outputWires: { ...(instance.outputs || {}) },
      });
      return node;
    }

    function wireProducers() {
      const producers = new Map();
      graph._nodes.forEach((node) => {
        const item = nodeMeta.get(node.id);
        if (item?.entity === "input") {
          const wire = String(node.properties.wire || "").trim();
          if (wire) producers.set(wire, { node, slot: 0 });
        }
        if (item?.entity === "module") {
          Object.keys(item.definition.ports?.outputs || {}).forEach((port, slot) => {
            const wire = String(item.outputWires[port] || "").trim();
            if (wire) producers.set(wire, { node, slot });
          });
        }
      });
      return producers;
    }

    function connectWire(targetNode, targetSlot, wire, producers) {
      const producer = producers.get(String(wire || "").trim());
      if (!producer) return;
      producer.node.connect(producer.slot, targetNode, targetSlot);
    }

    function rebuild(nextSnapshot = null) {
      suppress = true;
      canvas?.deselectAllNodes?.();
      selectedInspectorNodeId = null;
      activePortPicker = null;
      pendingModuleConnection = null;
      moduleSelect.placeholder = moduleConnectionPrompt();
      graph.clear();
      nodeMeta.clear();
      if (nextSnapshot) {
        currentInstances = clone(nextSnapshot.instances || {});
        currentDocument = graphDocument(nextSnapshot.alphaGraph);
      }
      let index = 0;
      Object.entries(currentDocument.inputs).forEach(([id, binding]) => addBoundary("input", id, binding, index++));
      currentDocument.nodes.forEach((instanceId) => {
        const instance = currentInstances[instanceId];
        if (!instance) return;
        const definition = definitionForInstance(instance) || inferredModuleDefinition(instance);
        addModule(instance, definition, index++);
      });
      Object.entries(currentDocument.outputs).forEach(([id, binding]) => addBoundary("output", id, binding, index++));
      const producers = wireProducers();
      graph._nodes.forEach((node) => {
        const item = nodeMeta.get(node.id);
        if (item?.entity === "module") {
          const instance = currentInstances[item.id];
          Object.keys(item.definition.ports?.inputs || {}).forEach((port, slot) => {
            connectWire(node, slot, instance?.inputs?.[port], producers);
          });
        } else if (item?.entity === "output") {
          connectWire(node, 0, currentDocument.outputs[item.id]?.wire, producers);
        }
      });
      shouldAutoLayout = !hasCompleteStoredLayout();
      if (initialized && shouldAutoLayout) layeredLayout();
      suppress = false;
      dirty = false;
      validateAndRender();
      renderInspector();
      requestAuthorityValidation(snapshot());
      syncStatus();
      graph.setDirtyCanvas(true, true);
    }

    function outputWire(node, slot) {
      const item = nodeMeta.get(node.id);
      if (item?.entity === "input") return String(node.properties.wire || "").trim();
      if (item?.entity !== "module") return "";
      const port = Object.keys(item.definition.ports?.outputs || {})[slot];
      if (!port) return "";
      if (!item.outputWires[port]) item.outputWires[port] = `${item.id}.${port}`;
      return item.outputWires[port];
    }

    function inputWire(node, slot) {
      const linkId = node.inputs?.[slot]?.link;
      const link = linkId == null ? null : graph.links[linkId];
      if (!link) return "";
      const source = graph.getNodeById(link.origin_id);
      return source ? outputWire(source, link.origin_slot) : "";
    }

    function snapshot() {
      const nextInstances = {};
      const document = { nodes: [], inputs: {}, outputs: {} };
      graph._nodes.forEach((node) => {
        const item = nodeMeta.get(node.id);
        if (!item) return;
        if (item.entity === "input") {
          document.inputs[item.id] = graphInputBoundary(
            node.properties.dataKey,
            node.properties.wire,
            node.properties.source,
            Boolean(namedInputSourceNames.length),
          );
          return;
        }
        if (item.entity === "output") {
          document.outputs[item.id] = {
            dataKey: String(node.properties.dataKey || "").trim(),
            wire: inputWire(node, 0),
          };
          return;
        }
        const definition = item.definition;
        const previous = currentInstances[item.id] || {};
        const instance = {
          instanceId: item.id,
          kind: definition.kind,
          moduleId: definition.moduleId,
          version: definition.version,
          config: { ...(node.properties || {}) },
          inputs: {},
          outputs: {},
        };
        Object.keys(definition.ports?.inputs || {}).forEach((port, slot) => {
          const wire = inputWire(node, slot);
          if (wire) instance.inputs[port] = wire;
        });
        Object.keys(definition.ports?.outputs || {}).forEach((port, slot) => {
          item.outputWires[port] ||= previous.outputs?.[port] || `${item.id}.${port}`;
          instance.outputs[port] = outputWire(node, slot);
        });
        nextInstances[item.id] = instance;
        document.nodes.push(item.id);
      });
      return { instances: nextInstances, alphaGraph: document };
    }

    function positionsSnapshot() {
      return Object.fromEntries(graph._nodes.map((node) => {
        const id = nodeMeta.get(node.id)?.id || `node-${node.id}`;
        return [id, { x: Math.round(node.pos?.[0] || 0), y: Math.round(node.pos?.[1] || 0) }];
      }));
    }

    function draftSignature(snapshotValue = null, editorValues = null) {
      return JSON.stringify({
        snapshot: snapshotValue || snapshot(),
        resourceValues: editorValues || resourceValues,
      });
    }

    function selectionSnapshot() {
      return selectedGraphNodes().map((node) => {
        const item = nodeMeta.get(node.id);
        return item ? `${item.entity}:${item.id}` : "";
      }).filter(Boolean);
    }

    function cameraSnapshot() {
      return {
        scale: Number(canvas.ds.scale) || 1,
        offset: [Number(canvas.ds.offset?.[0]) || 0, Number(canvas.ds.offset?.[1]) || 0],
      };
    }

    function syncHistoryActions() {
      const undo = root.querySelector("[data-graph-undo]");
      const redo = root.querySelector("[data-graph-redo]");
      if (undo) undo.disabled = historyIndex <= 0;
      if (redo) redo.disabled = historyIndex < 0 || historyIndex >= historyEntries.length - 1;
    }

    function recordHistory(nextSnapshot = null) {
      if (historyApplying || destroyed) return;
      const entry = {
        snapshot: clone(nextSnapshot || snapshot()),
        positions: positionsSnapshot(),
        selection: selectionSnapshot(),
        camera: cameraSnapshot(),
        resourceValues: clone(resourceValues),
      };
      const signature = JSON.stringify(entry);
      if (historyEntries[historyIndex]?.signature === signature) return;
      historyEntries.splice(historyIndex + 1);
      historyEntries.push({ ...entry, signature });
      if (historyEntries.length > HISTORY_LIMIT) historyEntries.shift();
      historyIndex = historyEntries.length - 1;
      syncHistoryActions();
    }

    function applyHistory(index) {
      const entry = historyEntries[index];
      if (!entry) return;
      historyApplying = true;
      localStorage.setItem(positionKey, JSON.stringify(entry.positions || {}));
      Object.keys(resourceValues).forEach((key) => {
        resourceValues[key] = String(entry.resourceValues?.[key] ?? resourceValues[key] ?? "");
      });
      resourceEditor?.onChange?.(clone(resourceValues));
      const contextNode = root.querySelector("[data-graph-context-name]");
      if (contextNode) contextNode.textContent = contextName();
      rebuild(entry.snapshot);
      if (entry.camera?.scale) canvas.ds.scale = entry.camera.scale;
      if (Array.isArray(entry.camera?.offset)) canvas.ds.offset = [...entry.camera.offset];
      const selectedRefs = new Set(entry.selection || []);
      const selected = graph._nodes.filter((node) => {
        const item = nodeMeta.get(node.id);
        return item && selectedRefs.has(`${item.entity}:${item.id}`);
      });
      if (selected.length) selectNodes(selected);
      historyIndex = index;
      dirty = draftSignature(entry.snapshot, entry.resourceValues) !== savedDraftSignature;
      syncStatus();
      onChange?.(clone(entry.snapshot));
      historyApplying = false;
      syncHistoryActions();
    }

    function undo() {
      if (historyIndex > 0) applyHistory(historyIndex - 1);
    }

    function redo() {
      if (historyIndex >= 0 && historyIndex < historyEntries.length - 1) applyHistory(historyIndex + 1);
    }

    function validation() {
      const issues = resourceFields
        .filter((field) => field.required && !String(resourceValues[field.key] || "").trim())
        .map((field) => ({
          level: "error",
          nodeId: null,
          message: `${field.label} is required.`,
        }));
      const seenWires = new Map();
      graph._nodes.forEach((node) => {
        const item = nodeMeta.get(node.id);
        if (!item) return;
        if (item.entity === "input") {
          const dataKey = String(node.properties.dataKey || "").trim();
          const wire = String(node.properties.wire || "").trim();
          if (!dataKey) issues.push({ level: "error", nodeId: node.id, message: "Graph Input requires a data key." });
          if (!wire) issues.push({ level: "error", nodeId: node.id, message: "Graph Input requires a wire." });
          if (
            namedInputSourceNames.length
            && !inputSourceNames.includes(String(node.properties.source || ""))
          ) {
            issues.push({ level: "error", nodeId: node.id, message: "Graph Input source is invalid." });
          }
          if (wire && seenWires.has(wire)) issues.push({ level: "error", nodeId: node.id, message: "A Graph connection has multiple producers." });
          if (wire) seenWires.set(wire, node.id);
          return;
        }
        if (item.entity === "output") {
          if (!String(node.properties.dataKey || "").trim()) issues.push({ level: "error", nodeId: node.id, message: "Graph Output requires a data key." });
          if (!inputWire(node, 0)) issues.push({ level: "error", nodeId: node.id, message: "Graph Output must be connected." });
          return;
        }
        Object.entries(item.definition.ports?.inputs || {}).forEach(([port, spec], slot) => {
          if (spec.required !== false && !inputWire(node, slot)) {
            issues.push({
              level: "error",
              nodeId: node.id,
              message: `${moduleDisplayName(item.definition)} input '${port}' is required.`,
            });
          }
        });
        Object.keys(item.definition.ports?.outputs || {}).forEach((port, slot) => {
          const wire = outputWire(node, slot);
          if (seenWires.has(wire)) issues.push({ level: "error", nodeId: node.id, message: "A Graph connection has multiple producers." });
          seenWires.set(wire, node.id);
        });
      });
      const moduleNodeIds = new Set(
        graph._nodes
          .filter((node) => nodeMeta.get(node.id)?.entity === "module")
          .map((node) => node.id),
      );
      const dependencies = new Map([...moduleNodeIds].map((nodeId) => [nodeId, new Set()]));
      Object.values(graph.links || {}).forEach((link) => {
        if (link && moduleNodeIds.has(link.origin_id) && moduleNodeIds.has(link.target_id)) {
          dependencies.get(link.target_id).add(link.origin_id);
        }
      });
      const remaining = new Set(moduleNodeIds);
      while (remaining.size) {
        const ready = [...remaining].filter((nodeId) => (
          ![...dependencies.get(nodeId)].some((dependency) => remaining.has(dependency))
        ));
        if (!ready.length) {
          issues.push({
            level: "error",
            nodeId: [...remaining][0],
            message: "Graph contains a Module dependency cycle.",
          });
          break;
        }
        ready.forEach((nodeId) => remaining.delete(nodeId));
      }
      return {
        errors: issues.filter((issue) => issue.level === "error"),
        warnings: issues.filter((issue) => issue.level === "warning"),
      };
    }

    function validateAndRender() {
      const result = validation();
      const engineErrors = !result.errors.length && authorityValidation.state === "invalid"
        ? [{ level: "error", nodeId: null, message: authorityValidation.message }]
        : [];
      const combined = { errors: [...result.errors, ...engineErrors], warnings: result.warnings };
      currentValidation = combined;
      root.__validationState = combined;
      if (result.errors.length) {
        validationElement.innerHTML = result.errors.slice(0, 4).map((issue) => `<button type="button" data-node-id="${issue.nodeId}" class="alpha-litegraph-validation-pill error">${escapeHtml(graphFacingText(issue.message, "Graph validation failed"))}</button>`).join("");
      } else if (authorityValidation.state === "invalid") {
        validationElement.innerHTML = `<span class="alpha-litegraph-validation-pill error">${escapeHtml(graphFacingText(authorityValidation.message, "Engine rejected this Graph"))}</span>`;
      } else {
        validationElement.innerHTML = `<span class="muted">${escapeHtml(graphFacingText(authorityValidation.message, "Graph validation unavailable"))}</span>`;
      }
      const saveButton = root.querySelector("[data-graph-save]");
      if (saveButton) {
        saveButton.disabled = Boolean(result.errors.length)
          || authorityValidation.state !== "valid";
      }
      return combined;
    }

    function nodeLabel(node) {
      const item = nodeMeta.get(node?.id);
      if (!item) return graphFacingText(node?.title, "Node");
      if (item.entity === "module") return moduleDisplayName(item.definition);
      return graphFacingText(node.properties?.dataKey, `Graph ${humanize(item.entity)}`);
    }

    function selectedGraphNodes() {
      return Object.values(canvas.selected_nodes || {}).filter((node) => node?.graph === graph);
    }

    function primarySelectedNode() {
      const selected = selectedGraphNodes();
      if (!selected.length) return null;
      if (canvas.node_selected && selected.some((node) => node.id === canvas.node_selected.id)) {
        return canvas.node_selected;
      }
      return selected[selected.length - 1];
    }

    function validationIssueForNode(nodeId) {
      return currentValidation.errors.find((issue) => issue.nodeId === nodeId)
        || currentValidation.warnings.find((issue) => issue.nodeId === nodeId)
        || null;
    }

    function explorerModeMatchesNode(node) {
      const item = nodeMeta.get(node.id);
      if (inspectorExplorerMode === "outputs") return item?.entity === "output";
      if (inspectorExplorerMode === "issues") return Boolean(validationIssueForNode(node.id));
      return true;
    }

    function explorerNodeMeta(node) {
      const item = nodeMeta.get(node.id);
      if (!item) return "";
      if (item.entity === "module") return modulePresentationMeta(item.definition);
      if (item.entity === "input") {
        const source = namedInputSourceNames.length
          ? inputSourceLabels[node.properties.source || ""] || defaultInputSourceLabel
          : "Graph input";
        return `input · ${graphFacingText(source, "Graph input")}`;
      }
      const wire = inputWire(node, 0);
      return wire ? `output · ${graphFacingText(wire, "connected wire")}` : "output · unconnected";
    }

    function filteredExplorerNodes() {
      const query = String(inspectorExplorerQuery || "").trim().toLowerCase();
      return [...graph._nodes]
        .filter((node) => nodeMeta.has(node.id) && explorerModeMatchesNode(node))
        .filter((node) => {
          if (!query) return true;
          const item = nodeMeta.get(node.id);
          const issue = validationIssueForNode(node.id);
          return [
            nodeLabel(node),
            item?.id,
            item?.definition?.moduleId,
            item?.definition?.name,
            explorerNodeMeta(node),
            issue?.message,
          ].some((value) => String(value || "").toLowerCase().includes(query));
        })
        .sort((left, right) => (
          (left.pos?.[1] || 0) - (right.pos?.[1] || 0)
          || (left.pos?.[0] || 0) - (right.pos?.[0] || 0)
        ));
    }

    function resourceInspectorSection() {
      if (!resourceFields.length) return "";
      return `
        <section class="alpha-litegraph-resource-editor" data-graph-resource-editor>
          <div class="alpha-litegraph-resource-editor-head">
            <strong>${escapeHtml(graphFacingText(resourceEditor.title, "Resource Details"))}</strong>
            ${resourceEditor.description ? `<span>${escapeHtml(graphFacingText(resourceEditor.description))}</span>` : ""}
          </div>
          <div class="alpha-litegraph-resource-fields">
            ${resourceFields.map((field) => `
              <label>
                <span>${escapeHtml(graphFacingText(field.label, "Field"))}</span>
                <input data-graph-resource-field="${escapeHtml(field.key)}"
                  value="${escapeHtml(isOpaqueMachineIdentity(resourceValues[field.key]) ? "" : resourceValues[field.key])}"
                  placeholder="${escapeHtml(isOpaqueMachineIdentity(resourceValues[field.key]) ? "Existing identity; type to replace" : graphFacingText(field.placeholder || ""))}"
                  ${field.required ? "required" : ""}
                  ${field.readOnly ? "readonly" : ""}
                  autocomplete="off" />
              </label>`).join("")}
          </div>
        </section>`;
    }

    function renderExplorer() {
      const nodes = filteredExplorerNodes();
      const outputCount = graph._nodes.filter((node) => nodeMeta.get(node.id)?.entity === "output").length;
      const issueCount = graph._nodes.filter((node) => validationIssueForNode(node.id)).length;
      inspectorExplorerActiveIndex = Math.max(
        0,
        Math.min(inspectorExplorerActiveIndex, Math.max(0, nodes.length - 1)),
      );
      inspectorElement.innerHTML = `
        ${resourceInspectorSection()}
        <div class="alpha-litegraph-inspector-card alpha-litegraph-inspector-empty">
          <div class="alpha-litegraph-explorer-mode">
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "all" ? "active" : ""}" data-graph-explorer-mode="all">All</button>
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "outputs" ? "active" : ""}" data-graph-explorer-mode="outputs">Outputs${outputCount ? ` (${outputCount})` : ""}</button>
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "issues" ? "active" : ""}" data-graph-explorer-mode="issues">Issues${issueCount ? ` (${issueCount})` : ""}</button>
          </div>
          <input type="text" class="alpha-litegraph-explorer-filter" data-graph-explorer-filter value="${escapeHtml(inspectorExplorerQuery)}" placeholder="Find by title, module, instance, output, or issue" />
          <div class="alpha-litegraph-explorer-list">
            ${nodes.length ? nodes.map((node, index) => {
              const issue = validationIssueForNode(node.id);
              return `<button type="button" class="alpha-litegraph-explorer-item ${index === inspectorExplorerActiveIndex ? "active" : ""}" data-node-id="${node.id}"><span class="alpha-litegraph-explorer-item-title">${escapeHtml(nodeLabel(node))}</span><span class="alpha-litegraph-explorer-item-meta">${escapeHtml(explorerNodeMeta(node))}</span>${issue ? `<span class="alpha-litegraph-explorer-item-issue ${escapeHtml(issue.level || "error")}">${escapeHtml(graphFacingText(issue.message, "Graph issue"))}</span>` : ""}</button>`;
            }).join("") : `<div class="muted">${inspectorExplorerMode === "issues" ? "No issue nodes match the current filter." : inspectorExplorerMode === "outputs" ? "No Graph Output nodes match the current filter." : "No nodes match the current filter."}</div>`}
          </div>
          <div class="alpha-litegraph-explorer-actions">
            <button type="button" class="alpha-litegraph-explorer-action" data-graph-explorer-action="arrange" ${nodes.length ? "" : "disabled"}>Arrange</button>
            <button type="button" class="alpha-litegraph-explorer-action" data-graph-explorer-action="select" ${nodes.length ? "" : "disabled"}>Select</button>
            <button type="button" class="alpha-litegraph-explorer-action" data-graph-explorer-action="frame" ${nodes.length ? "" : "disabled"}>Frame</button>
          </div>
        </div>`;
    }

    function renderMultipleSelection(selected) {
      inspectorElement.innerHTML = `
        ${resourceInspectorSection()}
        <div class="alpha-litegraph-inspector-card" data-graph-selected-detail>
          <div class="alpha-litegraph-inspector-head"><strong class="alpha-litegraph-inspector-title">${selected.length} nodes selected</strong><span class="muted">Use bulk actions, or pick one node for full details.</span></div>
          <div class="alpha-litegraph-inspector-actions alpha-litegraph-scope-actions">
            <button type="button" data-graph-selection-action="arrange">Arrange</button>
            <button type="button" data-graph-selection-action="frame">Frame</button>
          </div>
          <div class="alpha-litegraph-inspector-list">
            ${selected.map((node) => `<button type="button" class="alpha-litegraph-inspector-list-item" data-node-id="${node.id}"><span>${escapeHtml(nodeLabel(node))}</span><small>${escapeHtml(explorerNodeMeta(node))}</small></button>`).join("")}
          </div>
        </div>`;
    }

    function compatibleSourceRows(targetNode, targetSlot) {
      const targetType = targetNode.inputs?.[targetSlot]?.type || schemaToken({});
      return graph._nodes.flatMap((candidate) => (candidate.outputs || []).flatMap((output, slot) => {
        if (candidate.id === targetNode.id || !schemasCompatible(output.type, targetType)) return [];
        return [{ node: candidate, slot, output }];
      }));
    }

    function compatibleTargetRows(sourceNode, sourceSlot) {
      const sourceType = sourceNode.outputs?.[sourceSlot]?.type || 0;
      return graph._nodes.flatMap((candidate) => (candidate.inputs || []).flatMap((input, slot) => {
        if (candidate.id === sourceNode.id || !schemasCompatible(sourceType, input?.type || 0)) return [];
        return [{ node: candidate, slot, input }];
      }));
    }

    function renderInputPort(node, port, slot) {
      const linkId = port?.link;
      const link = linkId == null ? null : graph.links[linkId];
      const source = link ? graph.getNodeById(link.origin_id) : null;
      const sourcePort = source?.outputs?.[link.origin_slot];
      const pickerOpen = activePortPicker?.side === "input"
        && activePortPicker?.nodeId === node.id
        && activePortPicker?.slot === slot;
      const options = pickerOpen ? compatibleSourceRows(node, slot) : [];
      return `
        <div class="alpha-litegraph-port-row ${source ? "connected" : "empty"}">
          <div class="alpha-litegraph-port-head"><strong>${escapeHtml(port.name || `input ${slot + 1}`)}</strong><span class="alpha-litegraph-port-type">Input</span></div>
          ${source ? `<div class="alpha-litegraph-port-link"><span class="muted">From</span><button type="button" class="alpha-litegraph-port-node" data-node-id="${source.id}">${escapeHtml(nodeLabel(source))}</button><span class="alpha-litegraph-port-wire">${escapeHtml(sourcePort?.name || "output")}</span></div>` : '<div class="muted">Unconnected</div>'}
          <div class="alpha-litegraph-port-actions">
            <button type="button" class="alpha-litegraph-port-action" data-graph-port-action="add-source" data-node-id="${node.id}" data-port-slot="${slot}">${source ? "Replace" : "Connect"}</button>
            <button type="button" class="alpha-litegraph-port-action" data-graph-port-action="pick-source" data-node-id="${node.id}" data-port-slot="${slot}">Use Existing</button>
            ${source ? `<button type="button" class="alpha-litegraph-port-action alpha-litegraph-port-action-danger" data-graph-port-action="disconnect-input" data-node-id="${node.id}" data-port-slot="${slot}">Disconnect</button>` : ""}
          </div>
          ${pickerOpen ? `<div class="alpha-litegraph-port-picker"><span class="alpha-litegraph-port-picker-title">Compatible existing outputs</span><div class="alpha-litegraph-port-picker-list">${options.length ? options.map((row) => `<button type="button" class="alpha-litegraph-port-picker-item" data-graph-port-action="connect-source" data-node-id="${node.id}" data-port-slot="${slot}" data-source-node-id="${row.node.id}" data-source-slot="${row.slot}"><span class="alpha-litegraph-port-picker-item-title">${escapeHtml(nodeLabel(row.node))}.${escapeHtml(row.output.name || `output ${row.slot + 1}`)}</span><span class="alpha-litegraph-port-picker-item-meta">${escapeHtml(explorerNodeMeta(row.node))}</span></button>`).join("") : '<div class="muted">No compatible existing outputs</div>'}</div></div>` : ""}
        </div>`;
    }

    function renderOutputPort(node, port, slot) {
      const targets = (port.links || []).map((linkId) => {
        const link = graph.links[linkId];
        const target = link ? graph.getNodeById(link.target_id) : null;
        return target ? { linkId, target, input: target.inputs?.[link.target_slot] } : null;
      }).filter(Boolean);
      const pickerOpen = activePortPicker?.side === "output"
        && activePortPicker?.nodeId === node.id
        && activePortPicker?.slot === slot;
      const options = pickerOpen ? compatibleTargetRows(node, slot) : [];
      return `
        <div class="alpha-litegraph-port-row ${targets.length ? "connected" : "empty"}">
          <div class="alpha-litegraph-port-head"><strong>${escapeHtml(port.name || `output ${slot + 1}`)}</strong><span class="alpha-litegraph-port-type">${targets.length} connection(s)</span></div>
          <div class="alpha-litegraph-port-wire">${escapeHtml(graphFacingText(outputWire(node, slot), "No wire"))}</div>
          <div class="alpha-litegraph-port-actions">
            <button type="button" class="alpha-litegraph-port-action" data-graph-port-action="add-downstream" data-node-id="${node.id}" data-port-slot="${slot}">Add Downstream</button>
            <button type="button" class="alpha-litegraph-port-action" data-graph-port-action="pick-target" data-node-id="${node.id}" data-port-slot="${slot}">Use Existing</button>
          </div>
          ${targets.length ? `<div class="alpha-litegraph-port-targets">${targets.map((row) => `<span class="alpha-litegraph-port-target-chip"><button type="button" class="alpha-litegraph-port-node" data-node-id="${row.target.id}">${escapeHtml(nodeLabel(row.target))}.${escapeHtml(row.input?.name || "input")}</button><button type="button" class="alpha-litegraph-port-action alpha-litegraph-port-action-danger" data-graph-port-action="disconnect-link" data-link-id="${row.linkId}">Disconnect</button></span>`).join("")}</div>` : '<div class="muted">No downstream consumers</div>'}
          ${pickerOpen ? `<div class="alpha-litegraph-port-picker"><span class="alpha-litegraph-port-picker-title">Compatible existing inputs</span><div class="alpha-litegraph-port-picker-list">${options.length ? options.map((row) => `<button type="button" class="alpha-litegraph-port-picker-item" data-graph-port-action="connect-target" data-node-id="${node.id}" data-port-slot="${slot}" data-target-node-id="${row.node.id}" data-target-slot="${row.slot}"><span class="alpha-litegraph-port-picker-item-title">${escapeHtml(nodeLabel(row.node))}.${escapeHtml(row.input.name || `input ${row.slot + 1}`)}</span><span class="alpha-litegraph-port-picker-item-meta">${escapeHtml(explorerNodeMeta(row.node))}</span></button>`).join("") : '<div class="muted">No compatible existing inputs</div>'}</div></div>` : ""}
        </div>`;
    }

    function renderSingleSelection(node) {
      const item = nodeMeta.get(node.id);
      if (!item) return renderExplorer();
      const module = item.entity === "module";
      const issue = validationIssueForNode(node.id);
      const boundaryEditor = module ? "" : `
        <div class="alpha-litegraph-inspector-section">
          <strong class="alpha-litegraph-inspector-section-title">Boundary</strong>
          <label class="structured-field"><span>Data Key</span><input data-graph-boundary-field="dataKey" value="${escapeHtml(isGraphMachineIdentity(node.properties.dataKey) ? "" : (node.properties.dataKey || ""))}" placeholder="${isGraphMachineIdentity(node.properties.dataKey) ? "Existing data selection; type to replace" : ""}" /></label>
          ${item.entity === "input" ? `<label class="structured-field"><span>Wire</span><input data-graph-boundary-field="wire" value="${escapeHtml(isGraphMachineIdentity(node.properties.wire) ? "" : (node.properties.wire || ""))}" placeholder="${isGraphMachineIdentity(node.properties.wire) ? "Existing connection; type to replace" : ""}" /></label>` : ""}
          ${item.entity === "input" && namedInputSourceNames.length ? `<label class="structured-field"><span>Source</span><select data-graph-boundary-field="source">${Object.entries(inputSourceLabels).map(([source, label]) => `<option value="${escapeHtml(source)}" ${String(node.properties.source || "") === source ? "selected" : ""}>${escapeHtml(graphFacingText(label, "Input source"))}</option>`).join("")}</select></label>` : ""}
        </div>`;
      inspectorElement.innerHTML = `
        ${resourceInspectorSection()}
        <section class="alpha-litegraph-inspector-card" data-graph-selected-detail data-selected-node-id="${node.id}">
          <div class="alpha-litegraph-inspector-head"><strong class="alpha-litegraph-inspector-title">${escapeHtml(nodeLabel(node))}</strong><span class="muted">${escapeHtml(module ? modulePresentationMeta(item.definition) : `Graph ${humanize(item.entity)} boundary`)}</span></div>
          ${issue ? `<div class="alpha-litegraph-inspector-issue ${escapeHtml(issue.level || "error")}">${escapeHtml(graphFacingText(issue.message, "Graph issue"))}</div>` : ""}
          <div class="alpha-litegraph-inspector-kv"><div><span class="muted">Inputs</span><strong>${node.inputs?.length || 0}</strong></div><div><span class="muted">Outputs</span><strong>${node.outputs?.length || 0}</strong></div></div>
          ${boundaryEditor}
          ${module ? '<div class="alpha-litegraph-inspector-section" data-graph-config-editor><strong class="alpha-litegraph-inspector-section-title">Config</strong><div data-graph-config-fields></div><div class="dialog-error" data-graph-config-error hidden></div></div>' : ""}
          <div class="alpha-litegraph-inspector-section"><strong class="alpha-litegraph-inspector-section-title">Inputs</strong><div class="alpha-litegraph-port-list">${node.inputs?.length ? node.inputs.map((port, slot) => renderInputPort(node, port, slot)).join("") : '<div class="muted">No input ports</div>'}</div></div>
          <div class="alpha-litegraph-inspector-section"><strong class="alpha-litegraph-inspector-section-title">Outputs</strong><div class="alpha-litegraph-port-list">${node.outputs?.length ? node.outputs.map((port, slot) => renderOutputPort(node, port, slot)).join("") : '<div class="muted">No output ports</div>'}</div></div>
        </section>`;
      if (!module) return;
      const fields = inspectorElement.querySelector("[data-graph-config-fields]");
      const error = inspectorElement.querySelector("[data-graph-config-error]");
      forms.renderSchemaFields(fields, item.definition.configSchema || {}, clone(node.properties || {}));
      const maskedConfigValues = new WeakMap();
      const maskConfigIdentity = (control) => {
        const raw = String(control?.value || "");
        if (!control || !isGraphMachineIdentity(raw)) return;
        maskedConfigValues.set(control, raw);
        control.value = "";
        if (control.matches("input, textarea")) {
          control.placeholder = "Existing resource selection; type to replace";
          control.addEventListener("input", () => maskedConfigValues.delete(control), { once: true });
        }
      };
      fields.querySelectorAll("input, textarea, select").forEach(maskConfigIdentity);
      fields.addEventListener("change", (event) => {
        if (maskedConfigValues.has(event.target) && event.target.value) {
          maskedConfigValues.delete(event.target);
        }
        const restored = [];
        fields.querySelectorAll("input, textarea, select").forEach((control) => {
          const raw = maskedConfigValues.get(control);
          if (raw != null && !control.value) {
            control.value = raw;
            restored.push(control);
          }
        });
        try {
          node.properties = forms.readSchemaFields(fields, item.definition.configSchema || {});
          (node.widgets || []).forEach((widget) => {
            if (widget._tradeConfigKey && Object.prototype.hasOwnProperty.call(node.properties, widget._tradeConfigKey)) {
              widget.value = isGraphMachineIdentity(node.properties[widget._tradeConfigKey])
                ? "Existing resource selection"
                : node.properties[widget._tradeConfigKey];
            }
          });
          error.hidden = true;
          error.textContent = "";
          graph.setDirtyCanvas(true, true);
          scheduleEmit();
        } catch (exception) {
          error.textContent = graphFacingText(exception?.message, "Invalid Module configuration");
          error.hidden = false;
        } finally {
          restored.forEach((control) => {
            if (maskedConfigValues.has(control)) control.value = "";
          });
        }
      });
    }

    function bindResourceFieldEvents() {
      inspectorElement.querySelectorAll("[data-graph-resource-field]").forEach((input) => {
        input.addEventListener("input", () => {
          resourceValues[input.dataset.graphResourceField] = input.value;
          resourceEditor.onChange?.(clone(resourceValues));
          const contextNode = root.querySelector("[data-graph-context-name]");
          if (contextNode) contextNode.textContent = contextName();
          scheduleEmit();
        });
      });
    }

    function renderInspector() {
      const scrollTop = inspectorElement.scrollTop;
      const selected = selectedGraphNodes();
      if (!selected.length) renderExplorer();
      else if (selected.length > 1) renderMultipleSelection(selected);
      else renderSingleSelection(primarySelectedNode());
      bindResourceFieldEvents();
      inspectorElement.scrollTop = scrollTop;
    }

    function inspectorHasEditingFocus() {
      const active = document.activeElement;
      return Boolean(
        active
        && inspectorElement.contains(active)
        && active.matches("input, textarea, select, [contenteditable='true']"),
      );
    }

    function requestAuthorityValidation(next) {
      if (!actions.onValidate) {
        authorityValidation = {
          state: "unavailable",
          valid: false,
          message: "Engine validation is unavailable.",
        };
        validateAndRender();
        return Promise.resolve(authorityValidation);
      }
      const sequence = ++validationSequence;
      authorityValidation = { state: "pending", valid: false, message: "Validating with Engine…" };
      validateAndRender();
      authorityPromise = Promise.resolve(actions.onValidate(clone(next)))
        .then((response) => {
          if (sequence !== validationSequence) return authorityValidation;
          const internalOnly = response?.scope === "internal";
          authorityValidation = {
            state: response?.valid ? "valid" : "invalid",
            valid: Boolean(response?.valid),
            message: response?.valid
              ? (internalOnly
                ? "Internal Graph valid · external inputs unresolved"
                : "Engine-compiled Graph valid")
              : (response?.error || "Engine rejected this Graph."),
          };
          validateAndRender();
          if (!inspectorHasEditingFocus()) renderInspector();
          return authorityValidation;
        })
        .catch((exception) => {
          if (sequence !== validationSequence) return authorityValidation;
          authorityValidation = {
            state: "invalid",
            valid: false,
            message: exception?.message || "Engine rejected this Graph.",
          };
          validateAndRender();
          if (!inspectorHasEditingFocus()) renderInspector();
          return authorityValidation;
        });
      return authorityPromise;
    }

    function syncStatus(message = "") {
      statusElement.textContent = graphFacingText(message, dirty ? "Unsaved" : "Saved");
      statusElement.dataset.state = /fail|error/i.test(message)
        ? "error"
        : /saving/i.test(message)
          ? "saving"
          : dirty ? "unsaved" : "saved";
    }

    function emit() {
      clearTimeout(emitTimer);
      emitTimer = 0;
      if (destroyed) return null;
      const next = snapshot();
      currentInstances = clone(next.instances);
      currentDocument = clone(next.alphaGraph);
      root.__liteGraphLastSnapshot = clone(next);
      savePositions();
      validateAndRender();
      recordHistory(next);
      if (!inspectorHasEditingFocus()) renderInspector();
      onChange?.(clone(next));
      requestAuthorityValidation(next);
      return next;
    }

    function addBoundaryFromUi(kind) {
      const id = opaqueId(kind);
      const binding = kind === "input" ? { dataKey: "", wire: `input.${id}` } : { dataKey: "", wire: "" };
      const node = addBoundary(kind, id, binding, graph._nodes.length);
      node.pos = canvas.convertCanvasToOffset([canvasElement.width / 2, canvasElement.height / 2]);
      selectedInspectorNodeId = node.id;
      renderInspector();
      scheduleEmit();
      canvas.selectNode?.(node);
    }

    function addModuleFromUi() {
      const definition = selectedChooserModule();
      if (!definition) return;
      const connection = pendingModuleConnection ? { ...pendingModuleConnection } : null;
      const instanceId = opaqueId("module");
      const instance = {
        instanceId,
        kind: definition.kind,
        moduleId: definition.moduleId,
        version: definition.version,
        config: forms.schemaDefaults(definition.configSchema || {}),
        inputs: {},
        outputs: Object.fromEntries(
          Object.keys(definition.ports?.outputs || {}).map((port) => [port, `${instanceId}.${port}`]),
        ),
      };
      currentInstances[instanceId] = instance;
      const node = addModule(instance, definition, graph._nodes.length);
      const viewportCenter = canvas.convertCanvasToOffset([canvasElement.width / 2, canvasElement.height / 2]);
      node.pos = viewportCenter;
      if (connection?.sourceNodeId != null) {
        const source = graph.getNodeById(connection.sourceNodeId);
        const sourceOutput = source?.outputs?.[connection.sourceSlot];
        const compatibleInputs = (node.inputs || [])
          .map((input, slot) => ({ input, slot }))
          .filter(({ input }) => schemasCompatible(sourceOutput?.type || 0, input?.type || 0));
        const target = compatibleInputs.find(({ input }) => input.name === sourceOutput?.name)
          || compatibleInputs[0];
        if (source && target) {
          node.pos = [
            (source.pos?.[0] || 0) + (source.size?.[0] || 360) + 120,
            source.pos?.[1] || 0,
          ];
          source.connect(connection.sourceSlot, node, target.slot);
        }
      } else if (connection?.targetNodeId != null) {
        const target = graph.getNodeById(connection.targetNodeId);
        const targetInput = target?.inputs?.[connection.targetSlot];
        const compatibleOutputs = (node.outputs || [])
          .map((output, slot) => ({ output, slot }))
          .filter(({ output }) => schemasCompatible(output?.type || 0, targetInput?.type || 0));
        const source = compatibleOutputs.find(({ output }) => output.name === targetInput?.name)
          || compatibleOutputs[0];
        if (target && source) {
          node.pos = [
            (target.pos?.[0] || 0) - (node.size?.[0] || 360) - 120,
            target.pos?.[1] || 0,
          ];
          node.connect(source.slot, target, connection.targetSlot);
        }
      }
      selectedInspectorNodeId = node.id;
      renderInspector();
      scheduleEmit();
      canvas.selectNode?.(node);
      moduleSelect.value = "";
      delete moduleSelect.dataset.moduleKey;
      pendingModuleConnection = null;
      moduleSelect.placeholder = moduleConnectionPrompt();
      addModuleButton.disabled = true;
      closeModuleChooser();
    }

    function arrange() {
      layeredLayout();
    }

    function copySelection() {
      const selected = selectedGraphNodes();
      if (!selected.length) return false;
      const selectedIds = new Set(selected.map((node) => node.id));
      const minX = Math.min(...selected.map((node) => node.pos?.[0] || 0));
      const minY = Math.min(...selected.map((node) => node.pos?.[1] || 0));
      const snapshotValue = snapshot();
      const indexByNodeId = new Map(selected.map((node, index) => [node.id, index]));
      clipboard = {
        nodes: selected.map((node) => {
          const item = nodeMeta.get(node.id);
          return {
            entity: item.entity,
            definitionKey: item.entity === "module" ? moduleKey(item.definition) : "",
            instance: item.entity === "module" ? clone(snapshotValue.instances[item.id]) : null,
            properties: clone(node.properties || {}),
            offset: [(node.pos?.[0] || 0) - minX, (node.pos?.[1] || 0) - minY],
          };
        }),
        links: Object.values(graph.links || {}).filter((link) => (
          link && selectedIds.has(link.origin_id) && selectedIds.has(link.target_id)
        )).map((link) => ({
          origin: indexByNodeId.get(link.origin_id),
          originSlot: link.origin_slot,
          target: indexByNodeId.get(link.target_id),
          targetSlot: link.target_slot,
        })),
      };
      clipboardPasteCount = 0;
      return true;
    }

    function pasteSelection() {
      if (!clipboard?.nodes?.length) return false;
      clipboardPasteCount += 1;
      const center = canvas.convertCanvasToOffset([canvasElement.width / 2, canvasElement.height / 2]);
      const shift = 28 * clipboardPasteCount;
      const pasted = clipboard.nodes.map((row, index) => {
        let node;
        if (row.entity === "module") {
          const definition = definitionByKey.get(row.definitionKey);
          if (!definition) return null;
          const instanceId = opaqueId("module");
          const instance = {
            ...clone(row.instance || {}),
            instanceId,
            kind: definition.kind,
            moduleId: definition.moduleId,
            version: definition.version,
            inputs: {},
            outputs: Object.fromEntries(
              Object.keys(definition.ports?.outputs || {}).map((port) => [port, `${instanceId}.${port}`]),
            ),
          };
          currentInstances[instanceId] = instance;
          node = addModule(instance, definition, graph._nodes.length + index);
        } else {
          const id = opaqueId(row.entity);
          const binding = row.entity === "input"
            ? { dataKey: row.properties.dataKey, wire: `input.${id}`, source: row.properties.source }
            : { dataKey: row.properties.dataKey, wire: "" };
          node = addBoundary(row.entity, id, binding, graph._nodes.length + index);
        }
        node.pos = [center[0] + row.offset[0] + shift, center[1] + row.offset[1] + shift];
        return node;
      });
      clipboard.links.forEach((link) => {
        const source = pasted[link.origin];
        const target = pasted[link.target];
        if (source && target) source.connect(link.originSlot, target, link.targetSlot);
      });
      const valid = pasted.filter(Boolean);
      selectNodes(valid);
      scheduleEmit();
      return Boolean(valid.length);
    }

    function removeSelectedNodes() {
      const selected = selectedGraphNodes();
      if (!selected.length) return false;
      canvas.deselectAllNodes?.();
      selected.forEach((node) => graph.remove(node));
      selectedInspectorNodeId = null;
      activePortPicker = null;
      renderInspector();
      scheduleEmit();
      return true;
    }

    function resize() {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, stageElement.clientWidth);
      const height = Math.max(1, stageElement.clientHeight);
      canvasElement.width = Math.round(width * ratio);
      canvasElement.height = Math.round(height * ratio);
      canvasElement.style.width = `${width}px`;
      canvasElement.style.height = `${height}px`;
      canvas.resize?.();
      graph.setDirtyCanvas(true, true);
    }

    root.querySelector("[data-graph-input]").addEventListener("click", () => addBoundaryFromUi("input"));
    root.querySelector("[data-graph-output]").addEventListener("click", () => addBoundaryFromUi("output"));
    root.querySelector("[data-graph-rename]")?.addEventListener("click", () => {
      renderInspector();
      const field = root.querySelector('[data-graph-resource-field="name"]');
      field?.scrollIntoView({ block: "nearest" });
      field?.focus();
      field?.select();
    });
    syncModuleAddButton();
    addModuleButton.addEventListener("click", addModuleFromUi);
    root.querySelector("[data-graph-arrange]").addEventListener("click", arrange);
    root.querySelector("[data-graph-fit]").addEventListener("click", fitGraph);
    root.querySelector("[data-graph-fullscreen]").addEventListener("click", () => {
      graphFullscreen = !graphFullscreen;
      applyGraphLayoutState();
    });
    root.querySelector("[data-graph-toggle-explorer]").addEventListener("click", () => {
      explorerCollapsed = !explorerCollapsed;
      applyGraphLayoutState();
    });
    root.querySelector("[data-graph-undo]").addEventListener("click", undo);
    root.querySelector("[data-graph-redo]").addEventListener("click", redo);
    root.querySelector("[data-graph-back]").addEventListener("click", () => (actions.onBack || window.__tradePipelineActions?.backToPipeline)?.());
    root.querySelector("[data-graph-save]").addEventListener("click", async () => {
      emit();
      await authorityPromise;
      const result = validateAndRender();
      if (result.errors.length) return;
      syncStatus("Saving…");
      try {
        await saveAction?.();
        savedDraftSignature = draftSignature();
        dirty = false;
        syncStatus();
      } catch (error) {
        dirty = true;
        syncStatus(graphFacingText(error?.message, "Save failed"));
        showError(error);
      }
    });
    root.querySelector("[data-graph-load]").addEventListener("click", async () => {
      const version = root.querySelector("[data-graph-version]").value;
      if (version) await loadAction?.(version);
    });
    validationElement.addEventListener("click", (event) => {
      const node = graph.getNodeById(Number(event.target.closest("[data-node-id]")?.dataset.nodeId));
      if (node) revealNode(node);
    });
    inspectorElement.addEventListener("click", (event) => {
      const mode = event.target.closest("[data-graph-explorer-mode]")?.dataset.graphExplorerMode;
      if (mode) {
        inspectorExplorerMode = mode;
        inspectorExplorerActiveIndex = 0;
        renderInspector();
        return;
      }
      const explorerAction = event.target.closest("[data-graph-explorer-action]")?.dataset.graphExplorerAction;
      if (explorerAction) {
        const nodes = filteredExplorerNodes();
        if (explorerAction === "arrange") arrangeNodeSubset(nodes);
        if (explorerAction === "select") selectNodes(nodes);
        if (explorerAction === "frame") frameNodes(nodes);
        return;
      }
      const selectionAction = event.target.closest("[data-graph-selection-action]")?.dataset.graphSelectionAction;
      if (selectionAction) {
        const nodes = selectedGraphNodes();
        if (selectionAction === "arrange") arrangeNodeSubset(nodes);
        if (selectionAction === "frame") frameNodes(nodes);
        return;
      }
      const portActionNode = event.target.closest("[data-graph-port-action]");
      if (portActionNode) {
        const action = portActionNode.dataset.graphPortAction;
        const node = graph.getNodeById(Number(portActionNode.dataset.nodeId));
        const slot = Number(portActionNode.dataset.portSlot);
        if (action === "add-source" && node) {
          activePortPicker = null;
          focusModuleChooser({ targetNodeId: node.id, targetSlot: slot });
        } else if (action === "add-downstream" && node) {
          activePortPicker = null;
          focusModuleChooser({ sourceNodeId: node.id, sourceSlot: slot });
        } else if (action === "pick-source" && node) {
          activePortPicker = activePortPicker?.side === "input"
            && activePortPicker?.nodeId === node.id
            && activePortPicker?.slot === slot
            ? null : { side: "input", nodeId: node.id, slot };
          renderInspector();
        } else if (action === "pick-target" && node) {
          activePortPicker = activePortPicker?.side === "output"
            && activePortPicker?.nodeId === node.id
            && activePortPicker?.slot === slot
            ? null : { side: "output", nodeId: node.id, slot };
          renderInspector();
        } else if (action === "disconnect-input" && node) {
          node.disconnectInput?.(slot);
          activePortPicker = null;
          scheduleEmit();
          renderInspector();
        } else if (action === "connect-target" && node) {
          const target = graph.getNodeById(Number(portActionNode.dataset.targetNodeId));
          const targetSlot = Number(portActionNode.dataset.targetSlot);
          if (target) node.connect(slot, target, targetSlot);
          activePortPicker = null;
          scheduleEmit();
          renderInspector();
        } else if (action === "connect-source" && node) {
          const source = graph.getNodeById(Number(portActionNode.dataset.sourceNodeId));
          const sourceSlot = Number(portActionNode.dataset.sourceSlot);
          source?.connect(sourceSlot, node, slot);
          activePortPicker = null;
          scheduleEmit();
          renderInspector();
        } else if (action === "disconnect-link") {
          graph.removeLink?.(Number(portActionNode.dataset.linkId));
          scheduleEmit();
          renderInspector();
        }
        return;
      }
      const target = event.target.closest("[data-node-id]");
      const node = graph.getNodeById(Number(target?.dataset.nodeId));
      if (!node) return;
      const addToSelection = event.shiftKey || event.ctrlKey || event.metaKey;
      canvas.selectNode?.(node, addToSelection);
      selectedInspectorNodeId = node.id;
      const explorerRows = filteredExplorerNodes();
      const explorerIndex = explorerRows.findIndex((candidate) => candidate.id === node.id);
      if (explorerIndex >= 0) inspectorExplorerActiveIndex = explorerIndex;
      revealNode(node);
      renderInspector();
    });
    inspectorElement.addEventListener("input", (event) => {
      const filter = event.target.closest("[data-graph-explorer-filter]");
      if (filter) {
        inspectorExplorerQuery = filter.value;
        inspectorExplorerActiveIndex = 0;
        renderInspector();
        const next = inspectorElement.querySelector("[data-graph-explorer-filter]");
        next?.focus({ preventScroll: true });
        next?.setSelectionRange?.(next.value.length, next.value.length);
        return;
      }
      const boundary = event.target.closest("[data-graph-boundary-field]");
      if (!boundary) return;
      const selected = primarySelectedNode();
      const item = selected ? nodeMeta.get(selected.id) : null;
      if (!selected || item?.entity === "module") return;
      const key = boundary.dataset.graphBoundaryField;
      selected.properties[key] = boundary.value;
      if (key === "dataKey") {
        selected.title = boundary.value
          ? `${item.entity === "input" ? "Input" : "Output"}: ${graphFacingText(boundary.value, "Data key")}`
          : `Graph ${humanize(item.entity)}`;
        setNodeWidget(selected, 0, graphFacingText(boundary.value));
      } else if (key === "wire") {
        setNodeWidget(selected, 1, graphFacingText(boundary.value));
      }
      graph.setDirtyCanvas(true, true);
      scheduleEmit();
    });
    inspectorElement.addEventListener("change", (event) => {
      const boundary = event.target.closest('[data-graph-boundary-field="source"]');
      if (!boundary) return;
      const selected = primarySelectedNode();
      if (!selected) return;
      selected.properties.source = boundary.value;
      scheduleEmit();
    });
    inspectorElement.addEventListener("keydown", (event) => {
      const filter = event.target.closest("[data-graph-explorer-filter]");
      if (!filter) return;
      const nodes = filteredExplorerNodes();
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        if (nodes.length) {
          inspectorExplorerActiveIndex = (
            inspectorExplorerActiveIndex + direction + nodes.length
          ) % nodes.length;
          renderInspector();
          const next = inspectorElement.querySelector("[data-graph-explorer-filter]");
          next?.focus({ preventScroll: true });
          inspectorElement.querySelector(".alpha-litegraph-explorer-item.active")?.scrollIntoView({ block: "nearest" });
        }
      } else if (event.key === "Enter" && nodes.length) {
        event.preventDefault();
        selectNodes([nodes[inspectorExplorerActiveIndex] || nodes[0]]);
      } else if (event.key === "Escape" && inspectorExplorerQuery) {
        event.preventDefault();
        inspectorExplorerQuery = "";
        inspectorExplorerActiveIndex = 0;
        renderInspector();
        inspectorElement.querySelector("[data-graph-explorer-filter]")?.focus({ preventScroll: true });
      }
    });
    canvas.onSelectionChange = (selectedNodes) => {
      const selected = Object.values(selectedNodes || {}).at(-1) || null;
      selectedInspectorNodeId = selected?.id || null;
      activePortPicker = null;
      renderInspector();
    };
    graph.onNodeRemoved = () => {
      if (suppress) return;
      if (!graph.getNodeById(selectedInspectorNodeId)) selectedInspectorNodeId = null;
      renderInspector();
      scheduleEmit();
    };
    graph.onConnectionChange = () => {
      scheduleEmit();
      if (!suppress) renderInspector();
    };
    canvas.onNodeMoved = () => {
      savePositions();
      recordHistory();
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(stageElement);
    const onKeyDown = (event) => {
      if (!root.isConnected) return;
      const target = event.target;
      const typing = target?.matches?.("input, textarea, select, [contenteditable='true']");
      const command = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (event.key === "Escape" && graphFullscreen) {
        event.preventDefault();
        graphFullscreen = false;
        applyGraphLayoutState();
        return;
      }
      if (command && key === "k") {
        event.preventDefault();
        focusModuleChooser();
        return;
      }
      if (typing) return;
      if (command && key === "s") {
        event.preventDefault();
        root.querySelector("[data-graph-save]")?.click();
      } else if (command && key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
      } else if (command && key === "y") {
        event.preventDefault();
        redo();
      } else if (command && key === "a") {
        event.preventDefault();
        selectNodes([...graph._nodes]);
      } else if (command && key === "c") {
        event.preventDefault();
        copySelection();
      } else if (command && key === "x") {
        event.preventDefault();
        if (copySelection()) removeSelectedNodes();
      } else if (command && key === "v") {
        event.preventDefault();
        pasteSelection();
      } else if (command && key === "d") {
        event.preventDefault();
        if (copySelection()) pasteSelection();
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        removeSelectedNodes();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);

    root.__liteGraphGraph = graph;
    root.__liteGraphCanvas = canvas;
    root.__liteGraphNodeMeta = nodeMeta;
    root.__graphValidation = () => clone(validateAndRender());
    root.__flushPendingEmit = emit;
    root.__refreshLayout = resize;
    root.__syncSaveState = syncStatus;
    root.__setBlueprintStatus = (message, failed = false) => {
      statusElement.textContent = graphFacingText(message);
      statusElement.dataset.state = failed ? "error" : "saved";
    };
    root.__syncFromAlphaGraphSnapshot = (next) => {
      rebuild(next);
      historyEntries.splice(0);
      historyIndex = -1;
      recordHistory(snapshot());
      savedDraftSignature = draftSignature();
      dirty = false;
      syncStatus();
    };
    const cleanup = ({ flushPending = false } = {}) => {
      if (destroyed) return;
      if (flushPending) emit();
      destroyed = true;
      clearTimeout(emitTimer);
      resizeObserver.disconnect();
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", onModuleChooserDocumentPointerDown);
      if (LiteGraph.isValidConnection === tradeValidConnection) {
        LiteGraph.isValidConnection = previousValidConnection;
      }
      if (graphFullscreen) document.body.classList.remove("alpha-graph-modal-open");
      graph.stop?.();
      canvas.close?.();
      root.__liteGraphGraph = null;
      root.__liteGraphCanvas = null;
      root.__liteGraphNodeMeta = null;
      root.__moduleGraphCleanup = null;
      root.__alphaBlueprintCleanup = null;
    };
    root.__moduleGraphCleanup = cleanup;
    root.__alphaBlueprintCleanup = cleanup;

    rebuild();
    resize();
    try {
      const viewport = JSON.parse(localStorage.getItem(viewportKey) || "null");
      if (viewport?.scale) canvas.ds.scale = viewport.scale;
      if (Array.isArray(viewport?.offset)) canvas.ds.offset = [...viewport.offset];
    } catch {}
    initialized = true;
    if (shouldAutoLayout) layeredLayout();
    root.__liteGraphLastSnapshot = clone(snapshot());
    recordHistory(root.__liteGraphLastSnapshot);
    savedDraftSignature = draftSignature(root.__liteGraphLastSnapshot);
    dirty = false;
    syncStatus();
    applyGraphLayoutState();
    canvas.onRenderBackground = () => {
      localStorage.setItem(viewportKey, JSON.stringify({ scale: canvas.ds.scale, offset: [...canvas.ds.offset] }));
    };
    return { graph, canvas, emit };
  }

  window.ModuleGraphLiteGraph = { mount };
}());
