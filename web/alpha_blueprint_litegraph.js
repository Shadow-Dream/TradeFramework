(function () {
  const LiteGraph = window.LiteGraph;
  const forms = window.TradeModuleForms;
  const LEGACY_POSITIONS_KEY = "trade.pipeline.graph.positions.v2";
  const POSITIONS_KEY_PREFIX = "trade.pipeline.graph.positions.v3:";
  const VIEWPORT_KEY_PREFIX = "trade.pipeline.graph.viewport.v1:";
  const TYPE_PREFIX = "Alpha/";
  const ALPHA_GRAPH_FILTER = "trade-alpha";

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

  function defaultNullWire() {
    return "null";
  }

  function defaultOutputKey(moduleId, portName) {
    return `${moduleId}.${portName}`;
  }

  function slugToken(value, fallback = "output") {
    const normalized = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
    return normalized || fallback;
  }

  function uniqueDataKey(state, preferred) {
    const used = new Set();
    Object.keys(state?.outputs || {}).forEach((key) => {
      if (key) used.add(key);
    });
    Object.values(state?.instances || {}).forEach((instance) => {
      if (instance?.moduleId !== "graph-output") return;
      const dataKey = String(instance?.config?.dataKey || "").trim();
      if (dataKey) used.add(dataKey);
    });
    const base = slugToken(preferred, "output_live");
    if (!used.has(base)) return base;
    let index = 2;
    let candidate = `${base}_${index}`;
    while (used.has(candidate)) {
      index += 1;
      candidate = `${base}_${index}`;
    }
    return candidate;
  }

  function uniqueOutputKey(usedKeys, preferred) {
    const base = String(preferred || "").trim() || "output_live";
    if (!usedKeys.has(base)) {
      usedKeys.add(base);
      return base;
    }
    let index = 2;
    let candidate = `${base}_${index}`;
    while (usedKeys.has(candidate)) {
      index += 1;
      candidate = `${base}_${index}`;
    }
    usedKeys.add(candidate);
    return candidate;
  }

  function uniqueStrings(values = []) {
    return [...new Set(values.filter(Boolean))];
  }

  function readStoredPositions(storageKey = LEGACY_POSITIONS_KEY) {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || "{}");
    } catch {
      return {};
    }
  }

  function writeStoredPosition(instanceId, position, storageKey = LEGACY_POSITIONS_KEY) {
    const stored = readStoredPositions(storageKey);
    stored[instanceId] = {
      x: Math.round(position?.[0] ?? 32),
      y: Math.round(position?.[1] ?? 32),
    };
    localStorage.setItem(storageKey, JSON.stringify(stored));
  }

  function deleteStoredPosition(instanceId, storageKey = LEGACY_POSITIONS_KEY) {
    if (!instanceId) return;
    const stored = readStoredPositions(storageKey);
    if (!Object.prototype.hasOwnProperty.call(stored, instanceId)) return;
    delete stored[instanceId];
    localStorage.setItem(storageKey, JSON.stringify(stored));
  }

  function pruneStoredPositions(validInstanceIds = [], storageKey = LEGACY_POSITIONS_KEY) {
    const allowed = new Set((validInstanceIds || []).filter(Boolean));
    const stored = readStoredPositions(storageKey);
    let changed = false;
    Object.keys(stored).forEach((instanceId) => {
      if (allowed.has(instanceId)) return;
      delete stored[instanceId];
      changed = true;
    });
    if (changed) localStorage.setItem(storageKey, JSON.stringify(stored));
  }

  function migrateLegacyStoredPositions(storageKey, instanceIds = []) {
    if (!storageKey || storageKey === LEGACY_POSITIONS_KEY) return;
    const scoped = readStoredPositions(storageKey);
    if (Object.keys(scoped).length) return;
    const legacy = readStoredPositions(LEGACY_POSITIONS_KEY);
    const migrated = {};
    (instanceIds || []).forEach((instanceId) => {
      if (!instanceId || !legacy[instanceId]) return;
      migrated[instanceId] = legacy[instanceId];
    });
    if (Object.keys(migrated).length) {
      localStorage.setItem(storageKey, JSON.stringify(migrated));
    }
  }

  function typeNameForModule(module) {
    const id = moduleDisplayName(module) || "Node";
    const category = module?.moduleId === "graph-output"
      ? "Output"
      : module?.moduleId?.includes("indicator")
        ? "Indicators"
        : module?.moduleId?.includes("gate")
          ? "Logic"
          : module?.moduleId?.includes("source")
            ? "Sources"
            : "Nodes";
    return `${TYPE_PREFIX}${category}/${id}`;
  }

  function modulePalette(module) {
    if (module?.moduleId === "graph-output") {
      return {
        color: "#d7f3ef",
        bgcolor: "#eefbf7",
        boxcolor: "#0f766e",
      };
    }
    if (module?.moduleId?.includes("source")) {
      return {
        color: "#e0ecff",
        bgcolor: "#f2f7ff",
        boxcolor: "#1d4ed8",
      };
    }
    if (module?.moduleId?.includes("gate")) {
      return {
        color: "#ffe8d4",
        bgcolor: "#fff6ed",
        boxcolor: "#c2410c",
      };
    }
    if (module?.moduleId?.includes("indicator")) {
      return {
        color: "#e8f2dd",
        bgcolor: "#f7fbf2",
        boxcolor: "#4d7c0f",
      };
    }
    return {
      color: "#dfe6ea",
      bgcolor: "#f8fbfc",
      boxcolor: "#4b5563",
    };
  }

  function widgetTypeForSpec(spec = {}) {
    const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
    if (spec.enum?.length) return "combo";
    if (type === "boolean") return "toggle";
    if (type === "integer" || type === "number") return "number";
    return "text";
  }

  function normalizeWidgetValue(spec = {}, value) {
    if (value !== undefined) return value;
    if (Object.prototype.hasOwnProperty.call(spec, "default")) return spec.default;
    if (spec.enum?.length) return spec.enum[0];
    const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
    if (type === "boolean") return false;
    if (type === "integer" || type === "number") return 0;
    return "";
  }

  function categoryLabel(module) {
    return module?.moduleId === "graph-output" ? "Output" : "Signal";
  }

  function moduleDisplayName(module) {
    if (module?.moduleId === "graph-output") return "Graph Output";
    return humanize(module?.moduleId || "node");
  }

  function portTypeIsCompatible(outputType, inputType) {
    const source = String(outputType || "any");
    const target = String(inputType || "any");
    if (target === "any" || source === "any") return true;
    if (source === target) return true;
    if (target === "series.number" && (
      source.startsWith("series.price")
      || source.startsWith("series.volume")
      || source.startsWith("series.indicator")
    )) {
      return true;
    }
    return source.startsWith(`${target}.`);
  }

  function sortModules(modules) {
    return [...modules].sort((a, b) => {
      if (a.moduleId === "graph-output") return 1;
      if (b.moduleId === "graph-output") return -1;
      return String(a.moduleId || "").localeCompare(String(b.moduleId || ""));
    });
  }

  function moduleSearchScore(module, rawQuery) {
    const query = String(rawQuery || "").trim().toLowerCase();
    if (!query) return 0;
    const primaryFields = [
      moduleDisplayName(module),
      module.moduleId || "",
      typeNameForModule(module),
    ].map((value) => String(value || "").toLowerCase());
    const secondaryFields = [
      module.description || "",
      ...Object.entries(module?.ports?.inputs || {}).flatMap(([name, spec]) => [name, spec?.type || "any"]),
      ...Object.entries(module?.ports?.outputs || {}).flatMap(([name, spec]) => [name, spec?.type || "any"]),
    ].map((value) => String(value || "").toLowerCase());
    const rankField = (value, base) => {
      if (!value) return Number.POSITIVE_INFINITY;
      if (value === query) return base;
      if (value.startsWith(query)) return base + 1;
      const index = value.indexOf(query);
      if (index >= 0) return base + 2 + (index / 1000);
      return Number.POSITIVE_INFINITY;
    };

    let best = Number.POSITIVE_INFINITY;
    primaryFields.forEach((value) => {
      best = Math.min(best, rankField(value, 0));
    });
    secondaryFields.forEach((value) => {
      best = Math.min(best, rankField(value, 10));
    });
    return best;
  }

  function moduleContextScore(module, options = {}) {
    if (!module || !options || (!options.node_from && !options.node_to)) return 0;
    const inputSpecs = Object.values(module?.ports?.inputs || {});
    const outputSpecs = Object.values(module?.ports?.outputs || {});
    let score = 0;

    if (options.node_from) {
      const sourceSlot = slotIndexForSearchContext(options.node_from, options.slot_from, "output");
      const sourceType = options.node_from.outputs?.[sourceSlot]?.type || options.type_filter_in || "any";
      const compatibleInputs = inputSpecs.filter((spec) => portTypeIsCompatible(sourceType, spec?.type || "any"));
      if (compatibleInputs.length) {
        if (module.moduleId === "graph-output") score -= 100;
        if (!outputSpecs.length) score -= 6;
        score += inputSpecs.length / 100;
      }
    }

    if (options.node_to) {
      const targetSlot = slotIndexForSearchContext(options.node_to, options.slot_from, "input");
      const targetType = options.node_to.inputs?.[targetSlot]?.type || options.type_filter_out || "any";
      const compatibleOutputs = outputSpecs.filter((spec) => portTypeIsCompatible(spec?.type || "any", targetType));
      if (compatibleOutputs.length) {
        if (!inputSpecs.length) score -= 10;
        if (module.moduleId === "price-source") score -= 20;
        score += inputSpecs.length / 100;
      }
    }

    return score;
  }

  function moduleMatchesSearch(module, query) {
    return Number.isFinite(moduleSearchScore(module, query));
  }

  function summarizeModulePorts(module, side) {
    const ports = Object.keys(module?.ports?.[side] || {});
    if (!ports.length) return side === "inputs" ? "no inputs" : "no outputs";
    if (ports.length <= 4) return ports.join("/");
    return `${ports.slice(0, 4).join("/")} +${ports.length - 4}`;
  }

  function searchLabelForModule(module) {
    const primary = moduleDisplayName(module);
    const secondary = [
      module.moduleId || "",
      `in ${summarizeModulePorts(module, "inputs")}`,
      `out ${summarizeModulePorts(module, "outputs")}`,
    ].filter(Boolean).join(" · ");
    return [primary, secondary].filter(Boolean).join("\n");
  }

  function slotIndexForSearchContext(nodeRef, slotRef, side) {
    if (typeof slotRef === "number") return slotRef;
    if (typeof slotRef === "string") {
      return side === "output" ? nodeRef.findOutputSlot(slotRef) : nodeRef.findInputSlot(slotRef);
    }
    if (slotRef && typeof slotRef === "object") {
      if (typeof slotRef.slot_index === "number") return slotRef.slot_index;
      if (slotRef.name) {
        return side === "output" ? nodeRef.findOutputSlot(slotRef.name) : nodeRef.findInputSlot(slotRef.name);
      }
    }
    return 0;
  }

  function slotNameForReference(node, slotRef, side) {
    if (typeof slotRef === "string") return slotRef;
    if (slotRef && typeof slotRef === "object" && slotRef.name) return slotRef.name;
    const slotIndex = typeof slotRef === "number"
      ? slotRef
      : (slotRef && typeof slotRef === "object" && typeof slotRef.slot_index === "number")
        ? slotRef.slot_index
        : 0;
    const slots = side === "output" ? (node?.outputs || []) : (node?.inputs || []);
    return slots?.[slotIndex]?.name || `${side}_${slotIndex}`;
  }

  function searchContextForOptions(options = {}) {
    if (options.node_to) {
      const slotName = slotNameForReference(options.node_to, options.slot_from, "input");
      const nodeName = String(options.node_to?.title || "Node");
      return {
        title: "Find Upstream Node",
        detail: `Target ${nodeName}.${slotName}`,
      };
    }
    if (options.node_from) {
      const slotName = slotNameForReference(options.node_from, options.slot_from, "output");
      const nodeName = String(options.node_from?.title || "Node");
      return {
        title: "Add Downstream Node",
        detail: `Source ${nodeName}.${slotName}`,
      };
    }
    return {
      title: "Search Modules",
      detail: "Search by module, port, or type",
    };
  }

  function searchPlaceholderForOptions(options = {}) {
    if (options.node_to) return `Search producer modules for ${slotNameForReference(options.node_to, options.slot_from, "input")}`;
    if (options.node_from) return `Search downstream modules for ${slotNameForReference(options.node_from, options.slot_from, "output")}`;
    if (options.type_filter_in) return `Search modules with output ${options.type_filter_in}`;
    if (options.type_filter_out) return `Search modules with input ${options.type_filter_out}`;
    return "Search modules, ports, or types";
  }

  function cloneInitialState(modules, instances, alphaGraph) {
    const moduleById = new Map(modules.map((module) => [module.moduleId, module]));
    const requestedNodes = Array.isArray(alphaGraph?.nodes) ? alphaGraph.nodes.filter(Boolean) : null;
    const requested = requestedNodes?.length ? new Set(requestedNodes) : null;
    const nodeIds = [];
    const graphInstances = {};
    Object.values(instances || {}).forEach((instance) => {
      if (instance.kind !== "Signal") return;
      if (requested && !requested.has(instance.instanceId)) return;
      const module = moduleById.get(instance.moduleId);
      if (!module) return;
      graphInstances[instance.instanceId] = {
        instanceId: instance.instanceId,
        kind: instance.kind,
        moduleId: instance.moduleId,
        version: instance.version,
        config: { ...(instance.config || {}) },
        inputs: { ...(instance.inputs || {}) },
        outputs: { ...(instance.outputs || {}) },
        ports: module.ports || { inputs: {}, outputs: {} },
      };
      nodeIds.push(instance.instanceId);
    });
    return {
      instances: graphInstances,
      nodeIds: requested ? requestedNodes.filter((id) => graphInstances[id]) : nodeIds,
      outputs: JSON.parse(JSON.stringify(alphaGraph?.outputs || {})),
    };
  }

  function uniqueWireName(instances, preferred, currentInstanceId = "", currentPortName = "") {
    const used = new Set();
    Object.values(instances).forEach((instance) => {
      Object.entries(instance.outputs || {}).forEach(([portName, wire]) => {
        if (!wire) return;
        if (instance.instanceId === currentInstanceId && portName === currentPortName) return;
        used.add(wire);
      });
    });
    let candidate = preferred;
    let index = 2;
    while (used.has(candidate)) {
      candidate = `${preferred}.${index}`;
      index += 1;
    }
    return candidate;
  }

  function storedOrDefaultPosition(instanceId, index, storageKey = LEGACY_POSITIONS_KEY) {
    const stored = readStoredPositions(storageKey)[instanceId];
    if (stored) return [stored.x, stored.y];
    return [96 + (index % 4) * 420, 96 + Math.floor(index / 4) * 220];
  }

  function sharedBlueprintBusyState() {
    if (!window.__tradePipelineBlueprintBusyState) {
      window.__tradePipelineBlueprintBusyState = {
        attachInFlight: false,
        reloadInFlight: false,
      };
    }
    return window.__tradePipelineBlueprintBusyState;
  }

  function registerModuleTypes(modules, scheduleEmit) {
    modules.forEach((module) => {
      const type = typeNameForModule(module);
      if (LiteGraph.registered_node_types?.[type]) return;
      const properties = module.configSchema?.properties || {};
      class TradeAlphaNode extends LiteGraph.LGraphNode {
        constructor() {
          super();
          const palette = modulePalette(module);
          this.title = moduleDisplayName(module);
          this.size = module.moduleId === "graph-output" ? [280, 128] : [292, 148];
          this.color = palette.color;
          this.bgcolor = palette.bgcolor;
          this.boxcolor = palette.boxcolor;
          this.properties = {};
          Object.entries(properties).forEach(([name, spec]) => {
            this.properties[name] = normalizeWidgetValue(spec, undefined);
          });
          Object.entries(module.ports?.inputs || {}).forEach(([name, spec]) => {
            this.addInput(name, spec.type || "any");
          });
          Object.entries(module.ports?.outputs || {}).forEach(([name, spec]) => {
            this.addOutput(name, spec.type || "any");
          });
          Object.entries(properties).forEach(([name, spec]) => {
            const widgetType = widgetTypeForSpec(spec);
            const options = widgetType === "combo" ? { values: spec.enum || [] } : {};
            let widgetRef = null;
            widgetRef = this.addWidget(widgetType, humanize(name), this.properties[name], (value) => {
              if (widgetRef) widgetRef.value = value;
              this.properties[name] = value;
              if (module.moduleId === "graph-output" && name === "dataKey") {
                this.title = value ? `Output: ${value}` : "Graph Output";
              }
              scheduleEmit();
            }, options);
          });
          const computed = this.computeSize();
          this.size = [
            Math.max(computed[0], module.moduleId === "graph-output" ? 280 : 292),
            computed[1] + 10,
          ];
        }

        onConnectInput(inputIndex, outputType) {
          const portName = Object.keys(module.ports?.inputs || {})[inputIndex];
          const inputType = module.ports?.inputs?.[portName]?.type || "any";
          return portTypeIsCompatible(outputType, inputType);
        }

        onConnectionsChange() {
          scheduleEmit();
        }
      }
      TradeAlphaNode.title = moduleDisplayName(module);
      TradeAlphaNode.desc = module.description || categoryLabel(module);
      TradeAlphaNode.filter = ALPHA_GRAPH_FILTER;
      LiteGraph.registerNodeType(type, TradeAlphaNode);
    });
  }

  function buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey = LEGACY_POSITIONS_KEY) {
    const instances = {};
    const outputs = {};
    const graphOutputs = {};
    const usedOutputKeys = new Set();

    graph._nodes.forEach((node, index) => {
      const meta = nodeByGraphId.get(node.id);
      if (!meta) return;
      const module = meta.module;
      const portInputs = module.ports?.inputs || {};
      const portOutputs = module.ports?.outputs || {};
      const instance = {
        instanceId: meta.instanceId,
        kind: meta.kind,
        moduleId: meta.moduleId,
        version: meta.version,
        config: { ...(node.properties || {}) },
        inputs: {},
        outputs: {},
      };

      Object.keys(portOutputs).forEach((portName) => {
        const current = meta.outputWires[portName] || defaultOutputKey(meta.instanceId, portName);
        const wire = uniqueWireName(instances, current, meta.instanceId, portName);
        meta.outputWires[portName] = wire;
        instance.outputs[portName] = wire;
      });

      Object.keys(portInputs).forEach((portName, inputIndex) => {
        const inputSlot = (node.inputs || [])[inputIndex];
        const linkId = inputSlot?.link;
        const link = linkId != null ? graph.links[linkId] : null;
        if (!link) {
          instance.inputs[portName] = defaultNullWire();
          return;
        }
        const sourceMeta = nodeByGraphId.get(link.origin_id);
        if (!sourceMeta) {
          instance.inputs[portName] = defaultNullWire();
          return;
        }
        const sourcePortName = Object.keys(sourceMeta.module.ports?.outputs || {})[link.origin_slot];
        const wire = sourceMeta.outputWires[sourcePortName] || defaultOutputKey(sourceMeta.instanceId, sourcePortName);
        sourceMeta.outputWires[sourcePortName] = wire;
        instance.inputs[portName] = wire;
      });

      if (meta.moduleId === "graph-output") {
        const dataKey = String(instance.config.dataKey || "").trim();
        const wire = instance.inputs.value;
        if (dataKey) {
          usedOutputKeys.add(dataKey);
          graphOutputs[dataKey] ||= [];
          if (wire && wire !== defaultNullWire()) graphOutputs[dataKey].push(wire);
        }
      }

      instances[meta.instanceId] = instance;
      state.nodeIds[index] = meta.instanceId;
      writeStoredPosition(meta.instanceId, node.pos || [32, 32], positionsStorageKey);
    });

    Object.entries(graphOutputs).forEach(([name, wires]) => {
      outputs[name] = [...new Set(wires.filter(Boolean))];
    });

    graph._nodes.forEach((node) => {
      const meta = nodeByGraphId.get(node.id);
      if (!meta || meta.moduleId === "graph-output") return;
      const instance = instances[meta.instanceId];
      if (!instance) return;
      Object.keys(meta.module.ports?.outputs || {}).forEach((portName, outputIndex) => {
        const outputSlot = (node.outputs || [])[outputIndex];
        const hasDownstream = Boolean((outputSlot?.links || []).some((linkId) => graph.links?.[linkId]));
        if (hasDownstream) return;
        const wire = String(instance.outputs?.[portName] || "").trim();
        if (!wire || wire === defaultNullWire()) return;
        const dataKey = uniqueOutputKey(usedOutputKeys, wire);
        outputs[dataKey] = uniqueStrings([...(outputs[dataKey] || []), wire]);
      });
    });

    return {
      instances,
      alphaGraph: {
        nodes: graph._nodes.map((node) => nodeByGraphId.get(node.id)?.instanceId).filter(Boolean),
        outputs,
      },
    };
  }

  function mount(options) {
    const { root, modules, instances, alphaGraph, meta, onMetaChange, onChange } = options || {};
    if (!root || !LiteGraph?.LGraph || !LiteGraph?.LGraphCanvas) return null;
    root.__alphaBlueprintCleanup?.();

    LiteGraph.search_filter_enabled = true;
    LiteGraph.auto_load_slot_types = true;
    LiteGraph.middle_click_slot_add_default_node = true;
    LiteGraph.release_link_on_empty_shows_menu = false;

    const graphModules = sortModules((modules || []).filter((module) => module.kind === "Signal"));
    const moduleById = new Map(graphModules.map((module) => [module.moduleId, module]));
    const moduleByType = new Map(graphModules.map((module) => [typeNameForModule(module), module]));
    const state = cloneInitialState(graphModules, instances, alphaGraph);
    let emitTimer = 0;
    let suppressEmit = false;
    let suppressAutoStructureSync = 0;
    let markDirty = false;
    let historyApplying = false;
    let historyInitialized = false;
    let clipboardData = null;
    let clipboardPasteCount = 0;
    let attachInFlight = false;
    let reloadInFlight = false;
    let busyStateRestoring = false;
    let validationState = { errors: [], warnings: [] };
    let inspectorPortPicker = null;
    let inspectorExplorerQuery = "";
    let inspectorExplorerMode = "all";
    let inspectorExplorerActiveIndex = 0;
    let inspectorPortPickerActiveIndex = 0;
    let pendingEmitHistory = false;
    let pendingHistoryEntry = null;
    let suppressNextEmitHistory = false;
    let suppressHistoryRecording = false;
    let historyContextLock = false;
    let historyContextUnlockTimer = 0;
    let autoStructureCoalesceTimer = 0;
    let autoStructureCoalesceIndex = -1;
    const historyPast = [];
    const historyFuture = [];
    const HISTORY_LIMIT = 60;

    root.innerHTML = `
      <div class="alpha-litegraph-shell">
        <div class="alpha-litegraph-toolbar">
          <label class="alpha-litegraph-meta">
            <span>Lane</span>
            <input data-alpha-meta="laneId" value="${escapeHtml(meta?.laneId || "main")}" />
          </label>
          <label class="alpha-litegraph-meta">
            <span>Strategy</span>
            <input data-alpha-meta="strategyId" value="${escapeHtml(meta?.strategyId || "")}" />
          </label>
          <label class="alpha-litegraph-meta">
            <span>Version</span>
            <input data-alpha-meta="version" value="${escapeHtml(meta?.version || "")}" />
          </label>
          <label class="alpha-litegraph-meta alpha-litegraph-meta-name">
            <span>Name</span>
            <input data-alpha-meta="name" value="${escapeHtml(meta?.name || "")}" />
          </label>
          <label class="alpha-litegraph-picker">
            <span>Add Node</span>
            <select data-alpha-node-select>
              ${graphModules.map((module) => `<option value="${escapeHtml(module.moduleId)}">${escapeHtml(moduleDisplayName(module))}</option>`).join("")}
            </select>
          </label>
          <button type="button" data-alpha-add-node>Add</button>
          <button type="button" data-alpha-search>Search</button>
          <button type="button" data-alpha-undo>Undo</button>
          <button type="button" data-alpha-redo>Redo</button>
          <button type="button" data-alpha-reload>Reload</button>
          <button type="button" data-alpha-attach>Attach</button>
          <button type="button" data-alpha-arrange>Arrange</button>
          <button type="button" data-alpha-fit>Fit</button>
          <span class="alpha-litegraph-status muted" data-alpha-status></span>
          <span class="muted">Double-click canvas or Ctrl/Cmd+K to search. Double-click a port to add a compatible node. Ctrl/Cmd+S attaches. Ctrl/Cmd+C/X/V, Ctrl/Cmd+A, Ctrl/Cmd+D, and Backspace/Delete edit the selected graph.</span>
        </div>
        <div class="alpha-litegraph-validation" data-alpha-validation></div>
        <div class="alpha-litegraph-body">
          <div class="alpha-litegraph-stage">
            <canvas class="alpha-litegraph-canvas"></canvas>
          </div>
          <aside class="alpha-litegraph-inspector" data-alpha-inspector></aside>
        </div>
      </div>
    `;

    const shellEl = root.querySelector(".alpha-litegraph-shell");
    const canvasEl = root.querySelector(".alpha-litegraph-canvas");
    const stageEl = root.querySelector(".alpha-litegraph-stage");
    const toolbarEl = root.querySelector(".alpha-litegraph-toolbar");
    const statusEl = root.querySelector("[data-alpha-status]");
    const validationEl = root.querySelector("[data-alpha-validation]");
    const inspectorEl = root.querySelector("[data-alpha-inspector]");
    const graph = new LiteGraph.LGraph();
    graph.filter = ALPHA_GRAPH_FILTER;
    const canvas = new LiteGraph.LGraphCanvas(canvasEl, graph, {
      autoresize: false,
      background_image: null,
    });
    canvas.filter = ALPHA_GRAPH_FILTER;
    canvas.connections_width = 4;
    canvas.render_link_tooltip = true;
    canvas.allow_searchbox = true;
    canvas.allow_reconnect_links = true;
    const defaultCanvasInteractionFlags = {
      readOnly: false,
      allowDragCanvas: true,
      allowDragNodes: true,
      allowInteraction: true,
      allowSearchbox: true,
      allowReconnectLinks: true,
    };
    let viewportPlacementIndex = 0;
    let viewportPersistTimer = 0;

    function clearOrphanSearchBoxes(keep = null) {
      const doc = canvasEl.ownerDocument || document;
      doc.querySelectorAll(".litegraph.litesearchbox").forEach((dialog) => {
        if (dialog === keep) return;
        if (typeof dialog.close === "function") {
          dialog.close();
          return;
        }
        dialog.remove();
      });
    }

    function cloneJson(value) {
      return JSON.parse(JSON.stringify(value));
    }

    function scheduleViewportPersist(camera = null, metaState = null) {
      if (viewportPersistTimer) clearTimeout(viewportPersistTimer);
      viewportPersistTimer = window.setTimeout(() => {
        viewportPersistTimer = 0;
        writeStoredViewport(camera, metaState);
      }, 80);
    }

    function flushViewportPersist(camera = null, metaState = null) {
      if (viewportPersistTimer) {
        clearTimeout(viewportPersistTimer);
        viewportPersistTimer = 0;
      }
      writeStoredViewport(camera, metaState);
    }

    function updateFixedLayoutMetrics() {
      const toolbarHeight = toolbarEl?.offsetHeight || 54;
      const validationHeight = validationEl?.offsetHeight || 0;
      const target = shellEl || root;
      target.style.setProperty("--alpha-toolbar-height", `${toolbarHeight}px`);
      target.style.setProperty("--alpha-validation-height", `${validationHeight}px`);
      target.style.setProperty("--alpha-fixed-top", `${toolbarHeight + validationHeight}px`);
    }

    function currentMetaState() {
      return {
        laneId: root.querySelector('[data-alpha-meta="laneId"]')?.value?.trim() || "main",
        strategyId: root.querySelector('[data-alpha-meta="strategyId"]')?.value?.trim() || "",
        version: root.querySelector('[data-alpha-meta="version"]')?.value?.trim() || "",
        name: root.querySelector('[data-alpha-meta="name"]')?.value?.trim() || "",
      };
    }

    function viewportStorageKey(metaState = null) {
      const metaValue = metaState || currentMetaState();
      return `${VIEWPORT_KEY_PREFIX}${metaValue.laneId || "main"}::${metaValue.strategyId || ""}::${metaValue.version || ""}`;
    }

    function positionsStorageKey(metaState = null) {
      const metaValue = metaState || currentMetaState();
      return `${POSITIONS_KEY_PREFIX}${metaValue.laneId || "main"}::${metaValue.strategyId || ""}::${metaValue.version || ""}`;
    }

    function readStoredViewport(metaState = null) {
      try {
        const raw = localStorage.getItem(viewportStorageKey(metaState));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !Number.isFinite(parsed.scale) || !Array.isArray(parsed.offset) || parsed.offset.length < 2) return null;
        return {
          scale: parsed.scale,
          offset: [Number(parsed.offset[0]) || 0, Number(parsed.offset[1]) || 0],
        };
      } catch {
        return null;
      }
    }

    function writeStoredViewport(camera = null, metaState = null) {
      const nextCamera = camera || {
        scale: canvas.ds.scale,
        offset: [canvas.ds.offset[0], canvas.ds.offset[1]],
      };
      try {
        localStorage.setItem(viewportStorageKey(metaState), JSON.stringify({
          scale: Number(nextCamera.scale) || 1,
          offset: [
            Number(nextCamera.offset?.[0]) || 0,
            Number(nextCamera.offset?.[1]) || 0,
          ],
        }));
      } catch {}
    }

    function applyMetaState(nextMeta = {}) {
      const normalized = {
        laneId: String(nextMeta.laneId || "main"),
        strategyId: String(nextMeta.strategyId || ""),
        version: String(nextMeta.version || ""),
        name: String(nextMeta.name || ""),
      };
      const laneInput = root.querySelector('[data-alpha-meta="laneId"]');
      const strategyInput = root.querySelector('[data-alpha-meta="strategyId"]');
      const versionInput = root.querySelector('[data-alpha-meta="version"]');
      const nameInput = root.querySelector('[data-alpha-meta="name"]');
      if (laneInput) laneInput.value = normalized.laneId;
      if (strategyInput) strategyInput.value = normalized.strategyId;
      if (versionInput) versionInput.value = normalized.version;
      if (nameInput) nameInput.value = normalized.name;
      onMetaChange?.(normalized);
    }

    function viewportCenterGraphPos() {
      const width = canvasEl.width || stageEl.clientWidth || 1200;
      const height = canvasEl.height || stageEl.clientHeight || 720;
      return canvas.convertCanvasToOffset([width * 0.5, height * 0.5]);
    }

    function viewportGraphBounds() {
      const width = canvasEl.width || stageEl.clientWidth || 1200;
      const height = canvasEl.height || stageEl.clientHeight || 720;
      const topLeft = canvas.convertCanvasToOffset([0, 0]);
      const bottomRight = canvas.convertCanvasToOffset([width, height]);
      return {
        left: Math.min(topLeft[0], bottomRight[0]),
        top: Math.min(topLeft[1], bottomRight[1]),
        right: Math.max(topLeft[0], bottomRight[0]),
        bottom: Math.max(topLeft[1], bottomRight[1]),
      };
    }

    function nextViewportBasePosition(node) {
      const center = viewportCenterGraphPos();
      const nodeWidth = node?.size?.[0] || 292;
      const nodeHeight = node?.size?.[1] || 148;
      const column = viewportPlacementIndex % 3;
      const row = Math.floor(viewportPlacementIndex / 3) % 2;
      viewportPlacementIndex += 1;
      return [
        center[0] - nodeWidth * 0.5 + column * 72,
        center[1] - nodeHeight * 0.5 + row * 56,
      ];
    }

    function rectsOverlap(a, b, padding = 32) {
      return !(
        a.x + a.w + padding <= b.x
        || b.x + b.w + padding <= a.x
        || a.y + a.h + padding <= b.y
        || b.y + b.h + padding <= a.y
      );
    }

    function nodeRectAt(node, pos) {
      return {
        x: pos[0],
        y: pos[1],
        w: node?.size?.[0] || 292,
        h: node?.size?.[1] || 148,
      };
    }

    function clampNodePositionToViewport(node, pos) {
      const bounds = viewportGraphBounds();
      const margin = 24;
      const rect = nodeRectAt(node, pos);
      const minX = bounds.left + margin;
      const minY = bounds.top + margin;
      const maxX = Math.max(minX, bounds.right - rect.w - margin);
      const maxY = Math.max(minY, bounds.bottom - rect.h - margin);
      return [
        Math.min(Math.max(pos[0], minX), maxX),
        Math.min(Math.max(pos[1], minY), maxY),
      ];
    }

    function isNodePositionOpen(node, pos, ignoreNode = null) {
      const targetRect = nodeRectAt(node, pos);
      return !(graph._nodes || []).some((existing) => {
        if (!existing || existing === ignoreNode || existing === node) return false;
        return rectsOverlap(targetRect, nodeRectAt(existing, existing.pos || [0, 0]));
      });
    }

    function findOpenNodePosition(node, preferredPos, ignoreNode = null) {
      const clampedPreferred = clampNodePositionToViewport(node, preferredPos);
      if (isNodePositionOpen(node, clampedPreferred, ignoreNode)) return clampedPreferred;
      const stepX = Math.max((node?.size?.[0] || 292) + 48, 196);
      const stepY = Math.max((node?.size?.[1] || 148) + 40, 144);
      const rawCandidates = [];
      for (let ring = 1; ring <= 5; ring += 1) {
        for (let dx = -ring; dx <= ring; dx += 1) {
          for (let dy = -ring; dy <= ring; dy += 1) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
            const rawCandidate = [
              clampedPreferred[0] + dx * stepX,
              clampedPreferred[1] + dy * stepY,
            ];
            rawCandidates.push(rawCandidate);
            const candidate = clampNodePositionToViewport(node, [
              rawCandidate[0],
              rawCandidate[1],
            ]);
            if (isNodePositionOpen(node, candidate, ignoreNode)) return candidate;
          }
        }
      }
      for (const candidate of rawCandidates) {
        if (isNodePositionOpen(node, candidate, ignoreNode)) return candidate;
      }
      return clampedPreferred;
    }

    function ensureNodeVisible(node) {
      if (!node) return;
      const margin = 36;
      const topLeft = canvas.convertOffsetToCanvas(node.pos || [0, 0]);
      const bottomRight = canvas.convertOffsetToCanvas([
        (node.pos?.[0] || 0) + (node.size?.[0] || 292),
        (node.pos?.[1] || 0) + (node.size?.[1] || 148),
      ]);
      let shiftX = 0;
      let shiftY = 0;
      if (topLeft[0] < margin) shiftX = margin - topLeft[0];
      else if (bottomRight[0] > canvasEl.width - margin) shiftX = (canvasEl.width - margin) - bottomRight[0];
      if (topLeft[1] < margin) shiftY = margin - topLeft[1];
      else if (bottomRight[1] > canvasEl.height - margin) shiftY = (canvasEl.height - margin) - bottomRight[1];
      if (!shiftX && !shiftY) return;
      canvas.ds.offset[0] += shiftX / canvas.ds.scale;
      canvas.ds.offset[1] += shiftY / canvas.ds.scale;
      graph.setDirtyCanvas(true, true);
    }

    function focusNewNode(node) {
      if (!node) return;
      canvas.deselectAllNodes?.();
      canvas.selectNode?.(node);
      revealNode(node);
    }

    function revealNode(node) {
      if (!node) return;
      canvas.bringToFront?.(node);
      ensureNodeVisible(node);
    }

    const previousMouseDownCallback = canvas._mousedown_callback;
    const previousMouseMoveCallback = canvas._mousemove_callback;
    const previousMouseUpCallback = canvas._mouseup_callback;
    const originalShowSearchBox = canvas.showSearchBox.bind(canvas);
    const originalSetZoom = typeof canvas.setZoom === "function" ? canvas.setZoom.bind(canvas) : null;
    if (originalSetZoom) {
      canvas.setZoom = function patchedSetZoom(value, zoomingCenter) {
        const result = originalSetZoom(value, zoomingCenter);
        scheduleViewportPersist();
        return result;
      };
    }
    const originalProcessNodeSelected = typeof canvas.processNodeSelected === "function"
      ? canvas.processNodeSelected.bind(canvas)
      : null;
    if (originalProcessNodeSelected) {
      canvas.processNodeSelected = function patchedProcessNodeSelected(node, e) {
        const selectedNodes = Object.values(this.selected_nodes || {}).filter((candidate) => candidate?.graph === this.graph);
        if (
          node
          && node.is_selected
          && this.node_dragged === node
          && selectedNodes.length > 1
          && !e?.shiftKey
          && !e?.ctrlKey
          && !e?.metaKey
        ) {
          this.node_selected = node;
          this.onNodeSelected?.(node);
          return;
        }
        return originalProcessNodeSelected(node, e);
      };
    }
    const originalProcessMouseUp = typeof canvas.processMouseUp === "function" ? canvas.processMouseUp.bind(canvas) : null;
    if (originalProcessMouseUp) {
      canvas.processMouseUp = function patchedProcessMouseUp(e) {
        const wasDraggingCanvas = Boolean(this.dragging_canvas);
        const result = originalProcessMouseUp(e);
        if (wasDraggingCanvas) scheduleViewportPersist();
        return result;
      };
    }
    const originalProcessContextMenu = typeof canvas.processContextMenu === "function"
      ? canvas.processContextMenu.bind(canvas)
      : null;
    if (originalProcessContextMenu) {
      canvas.processContextMenu = function patchedProcessContextMenu(node, event) {
        const button = Number(event?.button);
        const which = Number(event?.which);
        const isExplicitRightClick = button === 2 || which === 3 || event?.type === "contextmenu";
        if (!isExplicitRightClick) return false;
        return originalProcessContextMenu(node, event);
      };
    }
    if (typeof canvas.processMouseDown === "function") {
      canvas._mousedown_callback = canvas.processMouseDown.bind(canvas);
    }
    if (typeof canvas.processMouseMove === "function") {
      canvas._mousemove_callback = canvas.processMouseMove.bind(canvas);
    }
    if (typeof canvas.processMouseUp === "function") {
      canvas._mouseup_callback = canvas.processMouseUp.bind(canvas);
    }
    if (previousMouseDownCallback) LiteGraph.pointerListenerRemove(canvasEl, "down", previousMouseDownCallback);
    if (previousMouseMoveCallback) LiteGraph.pointerListenerRemove(canvasEl, "move", previousMouseMoveCallback);
    if (previousMouseUpCallback) LiteGraph.pointerListenerRemove(canvasEl, "up", previousMouseUpCallback);
    LiteGraph.pointerListenerAdd(canvasEl, "down", canvas._mousedown_callback, true);
    LiteGraph.pointerListenerAdd(canvasEl, "move", canvas._mousemove_callback);
    LiteGraph.pointerListenerAdd(canvasEl, "up", canvas._mouseup_callback, true);
    canvas.showSearchBox = function patchedShowSearchBox(event, options = {}) {
      const button = Number(event?.button);
      const which = Number(event?.which);
      const isExplicitLeftMouse = button === 0 || which === 1;
      if (isExplicitLeftMouse && event?.type === "mousedown" && !Object.keys(options || {}).length) {
        return false;
      }
      clearOrphanSearchBoxes(this.search_box || null);
      LiteGraph.LGraphCanvas.active_canvas = this;
      this._tradeSearchOptions = options;
      const dialog = originalShowSearchBox(event, options);
      if (this.search_box) {
        this.search_box.__tradeSearchOptions = options;
        const context = searchContextForOptions(options);
        const titleEl = this.search_box.querySelector(".name");
        if (titleEl) titleEl.textContent = context.title;
        let contextEl = this.search_box.querySelector(".alpha-litegraph-search-context");
        if (!contextEl) {
          contextEl = document.createElement("div");
          contextEl.className = "alpha-litegraph-search-context";
          const helper = this.search_box.querySelector(".helper");
          if (helper?.parentNode) helper.parentNode.insertBefore(contextEl, helper);
        }
        if (contextEl) contextEl.textContent = context.detail;
        const input = this.search_box.querySelector("input");
        if (input) input.placeholder = searchPlaceholderForOptions(options);
        const helper = this.search_box.querySelector(".helper");
        const ensureSelectedSearchItem = () => {
          const items = [...(helper?.querySelectorAll(".lite-search-item") || [])];
          if (!items.length) return false;
          let selected = helper.querySelector(".lite-search-item.selected");
          if (!selected || !helper.contains(selected)) {
            items.forEach((item) => item.classList.remove("selected"));
            selected = items[0];
            selected.classList.add("selected");
          }
          selected.scrollIntoView({ block: "nearest" });
          return true;
        };
        if (helper) {
          const graphcanvas = this;
          const searchItems = () => [...helper.querySelectorAll(".lite-search-item")];
          const selectSearchLabel = (label) => {
            if (!label) return false;
            graphcanvas.onSearchBoxSelection?.(label, event, graphcanvas);
            graphcanvas.search_box?.close?.();
            return true;
          };
          const renderSearchResults = (query) => {
            if (typeof graphcanvas.onSearchBox !== "function") return false;
            const previousSelected = helper.querySelector(".lite-search-item.selected")?.dataset.alphaSearchLabel
              || helper.querySelector(".lite-search-item.selected")?.innerText
              || "";
            const previousQuery = helper.dataset.alphaSearchQuery || "";
            const labels = graphcanvas.onSearchBox(helper, query, graphcanvas) || [];
            helper.innerHTML = "";
            helper.dataset.alphaSearchQuery = String(query || "");
            labels.forEach((label) => {
              const item = document.createElement("div");
              item.className = "litegraph lite-search-item";
              item.innerText = label;
              item.dataset.alphaSearchLabel = label;
              item.addEventListener("click", () => {
                selectSearchLabel(label);
              });
              helper.appendChild(item);
            });
            if (!labels.length) {
              const empty = document.createElement("div");
              empty.className = "alpha-litegraph-search-empty";
              empty.textContent = "No matching modules";
              helper.appendChild(empty);
              return false;
            }
            const selectedIndex = query === previousQuery
              ? Math.max(0, labels.indexOf(previousSelected))
              : 0;
            activateSearchItemAt(selectedIndex);
            return true;
          };
          const activateSearchItemAt = (index) => {
            const items = searchItems();
            if (!items.length) return null;
            const nextIndex = Math.max(0, Math.min(index, items.length - 1));
            items.forEach((item, itemIndex) => item.classList.toggle("selected", itemIndex === nextIndex));
            items[nextIndex].scrollIntoView({ block: "nearest" });
            return items[nextIndex];
          };
          const selectedSearchItemIndex = () => {
            const items = searchItems();
            if (!items.length) return -1;
            const current = helper.querySelector(".lite-search-item.selected");
            return current ? items.indexOf(current) : -1;
          };
          const onSearchInputKeyDown = (event) => {
            if (!input || event.target !== input) return;
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              event.stopImmediatePropagation();
              this.search_box?.close?.();
              return;
            }
            if (!event.altKey && (
              event.key.length === 1
              || event.key === "Backspace"
              || event.key === "Delete"
              || ((event.ctrlKey || event.metaKey) && ["v", "x"].includes(String(event.key || "").toLowerCase()))
            )) {
              event.stopPropagation();
              event.stopImmediatePropagation();
              return;
            }
            if (event.key === "Enter" || (event.key === "Tab" && !event.shiftKey)) {
              renderSearchResults(input.value || "");
              const selected = helper.querySelector(".lite-search-item.selected") || activateSearchItemAt(0);
              const label = selected?.dataset.alphaSearchLabel || selected?.innerText || "";
              if (!label) return;
              event.preventDefault();
              event.stopPropagation();
              event.stopImmediatePropagation();
              selectSearchLabel(label);
              return;
            }
            const items = searchItems();
            if (!items.length) return;
            let nextIndex = selectedSearchItemIndex();
            if (event.key === "ArrowDown") nextIndex = Math.min((nextIndex >= 0 ? nextIndex : -1) + 1, items.length - 1);
            else if (event.key === "ArrowUp") nextIndex = Math.max((nextIndex >= 0 ? nextIndex : items.length) - 1, 0);
            else if (event.key === "Home") nextIndex = 0;
            else if (event.key === "End") nextIndex = items.length - 1;
            else if (event.key === "PageDown") nextIndex = Math.min((nextIndex >= 0 ? nextIndex : 0) + 6, items.length - 1);
            else if (event.key === "PageUp") nextIndex = Math.max((nextIndex >= 0 ? nextIndex : items.length - 1) - 6, 0);
            else return;
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            activateSearchItemAt(nextIndex);
          };
          const onSearchInput = () => {
            renderSearchResults(input?.value || "");
          };
          const onSearchHelperMouseMove = (event) => {
            const item = event.target.closest?.(".lite-search-item");
            if (!item || !helper.contains(item)) return;
            const items = searchItems();
            const index = items.indexOf(item);
            if (index >= 0) activateSearchItemAt(index);
          };
          input?.addEventListener("keydown", onSearchInputKeyDown, true);
          input?.addEventListener("input", onSearchInput);
          helper.addEventListener("mousemove", onSearchHelperMouseMove);
          const observer = new MutationObserver(() => {
            ensureSelectedSearchItem();
            window.requestAnimationFrame(ensureSelectedSearchItem);
          });
          observer.observe(helper, { childList: true });
          const originalClose = this.search_box.close?.bind(this.search_box);
          this.search_box.close = function patchedClose() {
            observer.disconnect();
            input?.removeEventListener("keydown", onSearchInputKeyDown, true);
            input?.removeEventListener("input", onSearchInput);
            helper.removeEventListener("mousemove", onSearchHelperMouseMove);
            return originalClose?.();
          };
          ensureSelectedSearchItem();
          window.requestAnimationFrame(ensureSelectedSearchItem);
        }
      }
      clearOrphanSearchBoxes(this.search_box || dialog || null);
      return dialog;
    };
    canvas.showConnectionMenu = function tradeConnectionMenu() {
      return false;
    };
    canvas.onSearchBox = function tradeSearchBox(_helper, rawQuery) {
      const options = this.search_box?.__tradeSearchOptions || this._tradeSearchOptions || {};
      const query = String(rawQuery || "").trim().toLowerCase();
      const inputFilter = options.type_filter_in || "";
      const outputFilter = options.type_filter_out || "";
      const entries = graphModules
        .filter((module) => {
          if (query && !moduleMatchesSearch(module, query)) return false;
          if (inputFilter) {
            const hasCompatibleInput = Object.values(module.ports?.inputs || {}).some((spec) => (
              portTypeIsCompatible(inputFilter, spec.type || "any")
            ));
            if (!hasCompatibleInput) return false;
          }
          if (outputFilter) {
            const hasCompatibleOutput = Object.values(module.ports?.outputs || {}).some((spec) => (
              portTypeIsCompatible(spec.type || "any", outputFilter)
            ));
            if (!hasCompatibleOutput) return false;
          }
          return true;
        })
        .map((module) => ({
          module,
          score: moduleSearchScore(module, query) + moduleContextScore(module, options),
        }))
        .sort((a, b) => a.score - b.score || String(a.module.moduleId || "").localeCompare(String(b.module.moduleId || "")))
        .map((module) => ({
          label: searchLabelForModule(module.module),
          type: typeNameForModule(module.module),
        }));
      const labelMap = Object.fromEntries(entries.map((entry) => [entry.label, entry.type]));
      if (this.search_box) {
        this.search_box.__tradeSearchEntryMap = labelMap;
      } else {
        this._tradeSearchEntryMap = labelMap;
      }
      return uniqueStrings(entries.map((entry) => entry.label));
    };

    function slotIndexFromReference(nodeRef, slotRef, side) {
      if (typeof slotRef === "number") return slotRef;
      if (typeof slotRef === "string") {
        return side === "output" ? nodeRef.findOutputSlot(slotRef) : nodeRef.findInputSlot(slotRef);
      }
      if (slotRef && typeof slotRef === "object") {
        if (typeof slotRef.slot_index === "number") return slotRef.slot_index;
        if (slotRef.name) {
          return side === "output" ? nodeRef.findOutputSlot(slotRef.name) : nodeRef.findInputSlot(slotRef.name);
        }
      }
      return 0;
    }

    function portPreferenceIndex(portName, preferredNames = []) {
      const slug = slugToken(portName, "");
      if (!slug) return Number.POSITIVE_INFINITY;
      const index = preferredNames.findIndex((name) => slug === slugToken(name, ""));
      return index >= 0 ? index : Number.POSITIVE_INFINITY;
    }

    function sourcePortInputPreferences(sourcePortName) {
      const sourceSlug = slugToken(sourcePortName, "");
      if (!sourceSlug) return ["value", "price", "close", "source", "input"];
      const exactNamePreferences = {
        price: ["price", "value", "close", "source", "input"],
        close: ["close", "price", "value", "source", "input"],
        open: ["open", "price", "value", "source", "input"],
        high: ["high", "price", "value", "source", "input"],
        low: ["low", "price", "value", "source", "input"],
        volume: ["volume", "value", "source", "input"],
      };
      return uniqueStrings([
        sourceSlug,
        ...(exactNamePreferences[sourceSlug] || ["value", "price", "close", "source", "input"]),
      ]);
    }

    function targetPortOutputPreferences(sourceNode, targetPortName) {
      const targetSlug = slugToken(targetPortName, "");
      const sourceMeta = nodeByGraphId.get(sourceNode?.id);
      const moduleStem = slugToken(String(sourceMeta?.moduleId || "").replace(/-(indicator|source|gate|insight)$/u, ""), "");
      const exactNamePreferences = {
        value: [moduleStem, "value", "price", "close", "middle", "signal", "macd", "rsi", "roc", "atr", "ema", "sma", "wma", "vwma", "direction", "insights"],
        price: ["price", "close", "value", moduleStem],
        close: ["close", "price", "value", moduleStem],
        open: ["open", "price", "value", moduleStem],
        high: ["high", "price", "value", moduleStem],
        low: ["low", "price", "value", moduleStem],
        volume: ["volume", "value", moduleStem],
      };
      return uniqueStrings([
        targetSlug,
        ...(exactNamePreferences[targetSlug] || [moduleStem, "value", "price", "close", targetSlug]),
      ]);
    }

    function firstCompatibleInputSlot(targetNode, sourceType, options = {}) {
      const inputs = targetNode.inputs || [];
      const sourcePortName = options.sourcePortName || "";
      const sourcePortSlug = slugToken(sourcePortName, "");
      const preferredNames = sourcePortInputPreferences(sourcePortName);
      const genericInputPreference = ["value", "price", "close", "source", "input", "signal", "fast", "slow", "high", "low", "open", "volume"];
      let bestIndex = -1;
      let bestScore = Number.POSITIVE_INFINITY;
      for (let i = 0; i < inputs.length; i += 1) {
        const input = inputs[i];
        if (!portTypeIsCompatible(sourceType, input?.type || "any")) continue;
        const inputSlug = slugToken(input?.name, "");
        let score = i;
        if (inputSlug && sourcePortSlug && inputSlug === sourcePortSlug) score -= 1000;
        const preferredIndex = portPreferenceIndex(input?.name, preferredNames);
        if (Number.isFinite(preferredIndex)) score -= 240 - (preferredIndex * 18);
        const genericIndex = portPreferenceIndex(input?.name, genericInputPreference);
        if (Number.isFinite(genericIndex)) score -= 40 - genericIndex;
        if ((input?.type || "any") === sourceType && sourceType !== "any") score -= 12;
        if (inputs.length === 1) score -= 20;
        if (score < bestScore) {
          bestScore = score;
          bestIndex = i;
        }
      }
      return bestIndex;
    }

    function firstCompatibleOutputSlot(sourceNode, targetType, options = {}) {
      const outputs = sourceNode.outputs || [];
      const targetPortName = options.targetPortName || "";
      const targetPortSlug = slugToken(targetPortName, "");
      const preferredNames = targetPortOutputPreferences(sourceNode, targetPortName);
      const genericOutputPreference = ["value", "price", "close", "open", "high", "low", "volume", "atr", "ema", "sma", "wma", "vwma", "rsi", "roc", "macd", "signal", "histogram", "obv", "k", "d", "middle", "upper", "lower", "direction", "insights"];
      let bestIndex = -1;
      let bestScore = Number.POSITIVE_INFINITY;
      for (let i = 0; i < outputs.length; i += 1) {
        const output = outputs[i];
        if (!portTypeIsCompatible(output?.type || "any", targetType)) continue;
        const outputSlug = slugToken(output?.name, "");
        let score = i;
        if (outputSlug && targetPortSlug && outputSlug === targetPortSlug) score -= 1000;
        const preferredIndex = portPreferenceIndex(output?.name, preferredNames);
        if (Number.isFinite(preferredIndex)) score -= 240 - (preferredIndex * 18);
        const genericIndex = portPreferenceIndex(output?.name, genericOutputPreference);
        if (Number.isFinite(genericIndex)) score -= 30 - genericIndex;
        if ((output?.type || "any") === targetType && targetType !== "any") score -= 12;
        if (outputs.length === 1) score -= 20;
        if (score < bestScore) {
          bestScore = score;
          bestIndex = i;
        }
      }
      return bestIndex;
    }

    function buildGraphOutputDataKey(sourceNode, sourceSlot) {
      const sourceMeta = nodeByGraphId.get(sourceNode?.id);
      const outputName = sourceNode?.outputs?.[sourceSlot]?.name || "output";
      const sourceModuleId = sourceMeta?.moduleId || "";
      const moduleStem = sourceModuleId.replace(/-(indicator|source|gate)$/u, "");
      const portStem = slugToken(outputName, "");
      const moduleStemSlug = slugToken(moduleStem, "");
      const preferred = portStem && portStem !== "value"
        ? `${portStem}_live`
        : moduleStemSlug
          ? `${moduleStemSlug}_live`
          : "output_live";
      return uniqueDataKey(state, preferred);
    }

    function defaultGraphOutputDataKey() {
      return uniqueDataKey(state, "output_live");
    }

    function uniqueInstanceId(moduleId) {
      const base = `${String(moduleId || "node")}.${Date.now().toString(36)}`;
      let candidate = base;
      let index = 2;
      while (state.instances?.[candidate] || state.nodeIds?.includes(candidate)) {
        candidate = `${base}.${index.toString(36)}`;
        index += 1;
      }
      return candidate;
    }

    function seedNewNode(node, module, options = {}) {
      if (module?.moduleId !== "graph-output") return;
      const currentDataKey = String(node.properties?.dataKey || "").trim();
      if (!currentDataKey) {
        const dataKey = options.sourceNode && Number.isInteger(options.sourceSlot) && options.sourceSlot >= 0
          ? buildGraphOutputDataKey(options.sourceNode, options.sourceSlot)
          : defaultGraphOutputDataKey();
        node.properties = { ...(node.properties || {}), dataKey };
      }
      const nextDataKey = String(node.properties?.dataKey || "").trim();
      if (nextDataKey) node.title = `Output: ${nextDataKey}`;
    }

    function positionNewNode(node, options, fallbackPos) {
      const nodeWidth = node.size?.[0] || 292;
      const nodeHeight = node.size?.[1] || 148;
      if (options.node_from) {
        const sourceSlot = slotIndexFromReference(options.node_from, options.slot_from, "output");
        const sourcePos = options.node_from.getConnectionPos(false, sourceSlot);
        return findOpenNodePosition(node, [sourcePos[0] + 180, sourcePos[1] - nodeHeight * 0.5], node);
      }
      if (options.node_to) {
        const targetSlot = slotIndexFromReference(options.node_to, options.slot_from, "input");
        const targetPos = options.node_to.getConnectionPos(true, targetSlot);
        return findOpenNodePosition(node, [targetPos[0] - nodeWidth - 180, targetPos[1] - nodeHeight * 0.5], node);
      }
      return findOpenNodePosition(node, fallbackPos, node);
    }

    canvas.onSearchBoxSelection = function tradeSearchSelection(name, event, graphcanvas) {
      const options = graphcanvas.search_box?.__tradeSearchOptions || graphcanvas._tradeSearchOptions || {};
      const entryMap = graphcanvas.search_box?.__tradeSearchEntryMap || graphcanvas._tradeSearchEntryMap || {};
      const resolvedType = entryMap?.[name] || name;
      const node = LiteGraph.createNode(resolvedType);
      if (!node) return;
      const module = moduleByType.get(resolvedType);
      const sourceSlot = options.node_from ? slotIndexFromReference(options.node_from, options.slot_from, "output") : -1;
      seedNewNode(node, module, {
        sourceNode: options.node_from || null,
        sourceSlot,
      });
      syncCurrentHistoryContext();
      withAutoStructureSyncSuppressed(() => {
        graphcanvas.graph.beforeChange();
        node.pos = positionNewNode(node, options, graphcanvas.convertEventToCanvasOffset(event));
        graphcanvas.graph.add(node, false);

        if (options.node_from) {
          const sourceSlot = slotIndexFromReference(options.node_from, options.slot_from, "output");
          const sourceType = options.node_from.outputs?.[sourceSlot]?.type || options.type_filter_in || "any";
          const targetSlot = firstCompatibleInputSlot(node, sourceType, {
            sourcePortName: options.node_from.outputs?.[sourceSlot]?.name || options.slot_from?.name || "",
          });
          if (sourceSlot >= 0 && targetSlot >= 0) {
            options.node_from.connect(sourceSlot, node, targetSlot);
          }
        }
        if (options.node_to) {
          const targetSlot = slotIndexFromReference(options.node_to, options.slot_from, "input");
          const targetType = options.node_to.inputs?.[targetSlot]?.type || options.type_filter_out || "any";
          const sourceSlot = firstCompatibleOutputSlot(node, targetType, {
            targetPortName: options.node_to.inputs?.[targetSlot]?.name || options.slot_from?.name || "",
          });
          if (sourceSlot >= 0 && targetSlot >= 0) {
            node.connect(sourceSlot, options.node_to, targetSlot);
          }
        }
      });

      focusNewNode(node);
      commitGraphStructureChange();
    };

    const nodeByGraphId = new Map();

    function currentPositionsByInstance() {
      return Object.fromEntries(graph._nodes
        .map((node) => {
          const instanceId = nodeByGraphId.get(node.id)?.instanceId;
          if (!instanceId) return null;
          return [instanceId, [node.pos?.[0] || 0, node.pos?.[1] || 0]];
        })
        .filter(Boolean));
    }

    function currentSelectionState() {
      const selectedInstanceIds = selectedGraphNodes()
        .map((node) => nodeByGraphId.get(node.id)?.instanceId || null)
        .filter(Boolean);
      const primaryInstanceId = nodeByGraphId.get(selectedPrimaryNode()?.id)?.instanceId || null;
      return {
        selectedInstanceIds,
        primaryInstanceId: primaryInstanceId && selectedInstanceIds.includes(primaryInstanceId)
          ? primaryInstanceId
          : (selectedInstanceIds[selectedInstanceIds.length - 1] || null),
      };
    }

    function updateStateFromSnapshot(snapshot) {
      const previousNodeIds = new Set(state.nodeIds || []);
      const nextNodeIds = new Set(snapshot.alphaGraph?.nodes || []);
      previousNodeIds.forEach((instanceId) => {
        if (!nextNodeIds.has(instanceId)) deleteStoredPosition(instanceId, positionsStorageKey());
      });
      pruneStoredPositions([...nextNodeIds], positionsStorageKey());
      state.instances = snapshot.instances;
      state.nodeIds = [...snapshot.alphaGraph.nodes];
      state.outputs = { ...(snapshot.alphaGraph.outputs || {}) };
      root.__liteGraphLastSnapshot = snapshot;
    }

    function historyEntryFromSnapshot(snapshot = null) {
      const resolvedSnapshot = snapshot || buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      return {
        snapshot: cloneJson(resolvedSnapshot),
        positions: cloneJson(currentPositionsByInstance()),
        camera: {
          scale: canvas.ds.scale,
          offset: [canvas.ds.offset[0], canvas.ds.offset[1]],
        },
        meta: currentMetaState(),
        selection: currentSelectionState(),
      };
    }

    function ensureHistoryBaseline() {
      if (historyInitialized) return;
      const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(snapshot);
      recordHistoryEntry(historyEntryFromSnapshot(snapshot), { resetFuture: true });
      historyInitialized = true;
    }

    function historySignature(entry) {
      return JSON.stringify(entry);
    }

    function historyStateSignature(entry) {
      return JSON.stringify({
        snapshot: entry?.snapshot || null,
        positions: entry?.positions || {},
        meta: entry?.meta || null,
      });
    }

    function releaseHistoryContextLock() {
      if (historyContextUnlockTimer) {
        clearTimeout(historyContextUnlockTimer);
        historyContextUnlockTimer = 0;
      }
      historyContextLock = false;
    }

    function clearAutoStructureCoalesce() {
      if (autoStructureCoalesceTimer) {
        clearTimeout(autoStructureCoalesceTimer);
        autoStructureCoalesceTimer = 0;
      }
      autoStructureCoalesceIndex = -1;
    }

    function withAutoStructureSyncSuppressed(callback) {
      suppressAutoStructureSync += 1;
      try {
        return callback();
      } finally {
        suppressAutoStructureSync = Math.max(0, suppressAutoStructureSync - 1);
      }
    }

    function recordHistoryEntry(entry, { resetFuture = true } = {}) {
      const signature = historySignature(entry);
      const last = historyPast[historyPast.length - 1];
      if (last && historySignature(last) === signature) return;
      historyPast.push(entry);
      if (historyPast.length > HISTORY_LIMIT) historyPast.splice(0, historyPast.length - HISTORY_LIMIT);
      if (resetFuture) historyFuture.length = 0;
    }

    function persistGraphNodePositions(graphNodesByInstanceId, storageKey = positionsStorageKey()) {
      if (!graphNodesByInstanceId?.size) return;
      graphNodesByInstanceId.forEach((node, instanceId) => {
        writeStoredPosition(instanceId, node?.pos || [32, 32], storageKey);
      });
    }

    function rebuildGraphFromSnapshot(snapshot, positions = {}, camera = null, nextMeta = null, selection = null) {
      historyApplying = true;
      suppressEmit = true;
      const previousNodeIds = [...(state.nodeIds || [])];
      graph.clear();
      nodeByGraphId.clear();
      state.instances = cloneJson(snapshot.instances || {});
      state.nodeIds = [...(snapshot.alphaGraph?.nodes || [])];
      state.outputs = cloneJson(snapshot.alphaGraph?.outputs || {});
      const nextNodeIds = new Set(state.nodeIds);
      previousNodeIds.forEach((instanceId) => {
        if (!nextNodeIds.has(instanceId)) deleteStoredPosition(instanceId, positionsStorageKey());
      });
      const graphNodesByInstanceId = new Map();
      state.nodeIds.forEach((instanceId, index) => {
        const instance = state.instances[instanceId];
        if (!instance) return;
        const node = createGraphNode(instance, index);
        if (!node) return;
        const storedPos = positions?.[instanceId];
        if (Array.isArray(storedPos) && storedPos.length >= 2) {
          node.pos = [storedPos[0], storedPos[1]];
        }
        graphNodesByInstanceId.set(instanceId, node);
      });
      persistGraphNodePositions(graphNodesByInstanceId, positionsStorageKey());
      state.nodeIds.forEach((instanceId) => {
        const instance = state.instances[instanceId];
        const node = graphNodesByInstanceId.get(instanceId);
        if (!instance || !node) return;
        Object.entries(instance.inputs || {}).forEach(([portName, wire]) => {
          if (!wire || wire === defaultNullWire()) return;
          const producerEntry = Object.values(state.instances).find((candidate) => Object.values(candidate.outputs || {}).includes(wire));
          if (!producerEntry) return;
          const sourceNode = graphNodesByInstanceId.get(producerEntry.instanceId);
          if (!sourceNode) return;
          const sourcePortName = Object.entries(producerEntry.outputs || {}).find(([, outputWire]) => outputWire === wire)?.[0];
          if (!sourcePortName) return;
          sourceNode.connect(sourcePortName, node, portName);
        });
      });
      if (camera?.scale) canvas.ds.scale = camera.scale;
      if (Array.isArray(camera?.offset) && camera.offset.length >= 2) {
        canvas.ds.offset[0] = camera.offset[0];
        canvas.ds.offset[1] = camera.offset[1];
      }
      const selectedInstanceIds = Array.isArray(selection?.selectedInstanceIds)
        ? selection.selectedInstanceIds.filter((instanceId) => graphNodesByInstanceId.has(instanceId))
        : [];
      const primaryInstanceId = selection?.primaryInstanceId && graphNodesByInstanceId.has(selection.primaryInstanceId)
        ? selection.primaryInstanceId
        : null;
      const selectionOrder = [
        ...selectedInstanceIds.filter((instanceId) => instanceId !== primaryInstanceId),
        ...(primaryInstanceId ? [primaryInstanceId] : []),
      ];
      canvas.deselectAllNodes?.();
      selectionOrder.forEach((instanceId, index) => {
        const node = graphNodesByInstanceId.get(instanceId);
        if (!node) return;
        canvas.selectNode?.(node, index > 0);
      });
      scheduleViewportPersist(camera || null, nextMeta || null);
      if (nextMeta) applyMetaState(nextMeta);
      suppressEmit = false;
      historyApplying = false;
      const nextSnapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(nextSnapshot);
      graph.setDirtyCanvas(true, true);
      refreshValidation();
      onChange?.(nextSnapshot);
      setStatus("Unsaved changes");
    }

    function restoreGraphToCurrentState() {
      if (busyStateRestoring) return;
      const baselineEntry = historyPast[historyPast.length - 1] || null;
      const snapshot = cloneJson(baselineEntry?.snapshot || root.__liteGraphLastSnapshot || {
        instances: cloneJson(state.instances || {}),
        alphaGraph: {
          nodes: [...(state.nodeIds || [])],
          outputs: cloneJson(state.outputs || {}),
        },
      });
      const camera = {
        scale: canvas.ds.scale,
        offset: [canvas.ds.offset[0], canvas.ds.offset[1]],
      };
      const metaState = currentMetaState();
      const selection = currentSelectionState();
      const positions = cloneJson(baselineEntry?.positions || readStoredPositions(positionsStorageKey(metaState)));
      const previousSuppressHistoryRecording = suppressHistoryRecording;
      busyStateRestoring = true;
      suppressHistoryRecording = true;
      try {
        rebuildGraphFromSnapshot(snapshot, positions, camera, metaState, selection);
      } finally {
        suppressHistoryRecording = previousSuppressHistoryRecording;
        busyStateRestoring = false;
      }
      setStatus(busyStatusMessage(), true);
    }

    function undoHistory() {
      ensureHistoryBaseline();
      clearAutoStructureCoalesce();
      releaseHistoryContextLock();
      if (emitTimer) {
        clearTimeout(emitTimer);
        emitTimer = 0;
      }
      pendingEmitHistory = false;
      pendingHistoryEntry = null;
      suppressNextEmitHistory = false;
      if (historyPast.length <= 1) return;
      const current = historyPast.pop();
      if (current) historyFuture.push(current);
      const previous = historyPast[historyPast.length - 1];
      if (!previous) return;
      suppressHistoryRecording = true;
      rebuildGraphFromSnapshot(previous.snapshot, previous.positions, previous.camera, previous.meta, previous.selection);
      window.setTimeout(() => {
        suppressHistoryRecording = false;
      }, 0);
    }

    function redoHistory() {
      ensureHistoryBaseline();
      clearAutoStructureCoalesce();
      releaseHistoryContextLock();
      if (emitTimer) {
        clearTimeout(emitTimer);
        emitTimer = 0;
      }
      pendingEmitHistory = false;
      pendingHistoryEntry = null;
      suppressNextEmitHistory = false;
      if (!historyFuture.length) return;
      const next = historyFuture.pop();
      if (!next) return;
      historyPast.push(next);
      suppressHistoryRecording = true;
      rebuildGraphFromSnapshot(next.snapshot, next.positions, next.camera, next.meta, next.selection);
      window.setTimeout(() => {
        suppressHistoryRecording = false;
      }, 0);
    }

    function queueEmit({ recordHistory = true } = {}) {
      if (suppressEmit) return;
      if (busyStateRestoring) return;
      if (isBlueprintBusy()) {
        pendingEmitHistory = false;
        pendingHistoryEntry = null;
        if (emitTimer) {
          clearTimeout(emitTimer);
          emitTimer = 0;
        }
        restoreGraphToCurrentState();
        return;
      }
      let effectiveRecordHistory = recordHistory;
      if (recordHistory && suppressNextEmitHistory) {
        effectiveRecordHistory = false;
        suppressNextEmitHistory = false;
      }
      if (effectiveRecordHistory && !historyApplying && !suppressHistoryRecording) {
        const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
        pendingHistoryEntry = historyEntryFromSnapshot(snapshot);
      }
      pendingEmitHistory ||= effectiveRecordHistory;
      if (emitTimer) clearTimeout(emitTimer);
      emitTimer = window.setTimeout(() => {
        flushPendingEmit();
      }, 0);
    }

    function flushPendingEmit() {
      if (suppressEmit || busyStateRestoring) return null;
      if (emitTimer) {
        clearTimeout(emitTimer);
        emitTimer = 0;
      }
      const shouldRecordHistory = pendingEmitHistory;
      const entryToRecord = pendingHistoryEntry;
      pendingEmitHistory = false;
      pendingHistoryEntry = null;
      const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(snapshot);
      ensureHistoryBaseline();
      if (shouldRecordHistory && entryToRecord && !historyApplying && !suppressHistoryRecording) {
        recordHistoryEntry(entryToRecord);
      }
      if (markDirty) setStatus("Unsaved changes");
      refreshValidation();
      onChange?.(snapshot);
      return snapshot;
    }

    function recordCurrentGraphHistoryEntry() {
      const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(snapshot);
      ensureHistoryBaseline();
      if (!historyApplying && !suppressHistoryRecording) {
        recordHistoryEntry(historyEntryFromSnapshot(snapshot));
      }
      return snapshot;
    }

    function syncCurrentHistoryContext({ force = false } = {}) {
      ensureHistoryBaseline();
      if (historyApplying || suppressHistoryRecording) return null;
      if (!force && historyContextLock) return null;
      if (!force) {
        historyContextLock = true;
        if (historyContextUnlockTimer) clearTimeout(historyContextUnlockTimer);
        historyContextUnlockTimer = window.setTimeout(() => {
          historyContextUnlockTimer = 0;
          historyContextLock = false;
        }, 0);
      }
      const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(snapshot);
      const entry = historyEntryFromSnapshot(snapshot);
      const last = historyPast[historyPast.length - 1];
      if (!last) {
        historyPast.push(entry);
        return entry;
      }
      if (historyStateSignature(last) === historyStateSignature(entry)) {
        historyPast[historyPast.length - 1] = entry;
        return entry;
      }
      recordHistoryEntry(entry);
      return entry;
    }

    function commitGraphStructureChange(options = {}) {
      if (busyStateRestoring) return root.__liteGraphLastSnapshot || null;
      if (isBlueprintBusy()) {
        clearAutoStructureCoalesce();
        releaseHistoryContextLock();
        restoreGraphToCurrentState();
        return root.__liteGraphLastSnapshot || null;
      }
      const { coalesceHistory = false } = options;
      if (!coalesceHistory) clearAutoStructureCoalesce();
      releaseHistoryContextLock();
      if (emitTimer) {
        clearTimeout(emitTimer);
        emitTimer = 0;
      }
      pendingEmitHistory = false;
      pendingHistoryEntry = null;
      const snapshot = buildInstancesFromGraph(graph, state, nodeByGraphId, positionsStorageKey());
      updateStateFromSnapshot(snapshot);
      ensureHistoryBaseline();
      if (!historyApplying && !suppressHistoryRecording) {
        const entry = historyEntryFromSnapshot(snapshot);
        if (coalesceHistory) {
          if (autoStructureCoalesceIndex >= 0 && autoStructureCoalesceIndex < historyPast.length) {
            historyPast[autoStructureCoalesceIndex] = entry;
            historyFuture.length = 0;
          } else {
            recordHistoryEntry(entry);
            autoStructureCoalesceIndex = historyPast.length - 1;
          }
          if (autoStructureCoalesceTimer) clearTimeout(autoStructureCoalesceTimer);
          autoStructureCoalesceTimer = window.setTimeout(() => {
            autoStructureCoalesceTimer = 0;
            autoStructureCoalesceIndex = -1;
          }, 0);
        } else {
          recordHistoryEntry(entry);
        }
      }
      if (markDirty) setStatus("Unsaved changes");
      refreshValidation();
      onChange?.(snapshot);
      return snapshot;
    }

    function commitNodePositionChange() {
      if (busyStateRestoring) return root.__liteGraphLastSnapshot || null;
      if (isBlueprintBusy()) {
        releaseHistoryContextLock();
        restoreGraphToCurrentState();
        return root.__liteGraphLastSnapshot || null;
      }
      releaseHistoryContextLock();
      const graphNodesByInstanceId = new Map(
        (graph._nodes || [])
          .map((node) => {
            const instanceId = nodeByGraphId.get(node.id)?.instanceId;
            return instanceId ? [instanceId, node] : null;
          })
          .filter(Boolean),
      );
      persistGraphNodePositions(graphNodesByInstanceId);
      recordCurrentGraphHistoryEntry();
      suppressNextEmitHistory = true;
      queueEmit({ recordHistory: false });
    }

    function setStatus(text, isError = false) {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.classList.toggle("error", Boolean(isError));
      updateFixedLayoutMetrics();
    }

    function isAttachInFlight() {
      return attachInFlight || sharedBlueprintBusyState().attachInFlight;
    }

    function isReloadInFlight() {
      return reloadInFlight || sharedBlueprintBusyState().reloadInFlight;
    }

    function syncSharedBusyState() {
      const shared = sharedBlueprintBusyState();
      shared.attachInFlight = Boolean(attachInFlight);
      shared.reloadInFlight = Boolean(reloadInFlight);
      window.__syncPipelineComposerActionState?.();
      document.querySelector("#alphaGraphBuilder")?.__syncAttachState?.();
    }

    function busyStatusMessage() {
      if (isReloadInFlight()) return "Reload in progress";
      if (isAttachInFlight()) return "Attach in progress";
      return "";
    }

    function isBlueprintBusy() {
      return Boolean(busyStatusMessage());
    }

    function syncBusyUiState() {
      const busyMessage = busyStatusMessage();
      const busy = Boolean(busyMessage);
      canvas.read_only = busy ? true : defaultCanvasInteractionFlags.readOnly;
      canvas.allow_dragcanvas = busy ? false : defaultCanvasInteractionFlags.allowDragCanvas;
      canvas.allow_dragnodes = busy ? false : defaultCanvasInteractionFlags.allowDragNodes;
      canvas.allow_interaction = busy ? false : defaultCanvasInteractionFlags.allowInteraction;
      canvas.allow_searchbox = busy ? false : defaultCanvasInteractionFlags.allowSearchbox;
      canvas.allow_reconnect_links = busy ? false : defaultCanvasInteractionFlags.allowReconnectLinks;
      shellEl?.classList.toggle("alpha-litegraph-shell-busy", busy);
      stageEl?.classList.toggle("alpha-litegraph-stage-busy", busy);
      inspectorEl?.classList.toggle("alpha-litegraph-inspector-busy", busy);
      if (stageEl) stageEl.style.pointerEvents = busy ? "none" : "";
      if (inspectorEl) inspectorEl.style.pointerEvents = busy ? "none" : "";
      root.querySelectorAll([
        "[data-alpha-meta]",
        "[data-alpha-node-select]",
        "[data-alpha-add-node]",
        "[data-alpha-search]",
        "[data-alpha-undo]",
        "[data-alpha-redo]",
        "[data-alpha-arrange]",
        "[data-alpha-fit]",
      ].join(", ")).forEach((element) => {
        if (!("disabled" in element)) return;
        element.disabled = busy;
        element.title = busy ? busyMessage : "";
      });
      inspectorEl?.querySelectorAll("button, input, select, textarea").forEach((element) => {
        if (!("disabled" in element)) return;
        if (!element.dataset.alphaBusyDefaultCaptured) {
          element.dataset.alphaBusyDefaultCaptured = "1";
          element.dataset.alphaBusyDefaultDisabled = element.disabled ? "1" : "0";
          element.dataset.alphaBusyDefaultTitle = element.title || "";
        }
        if (busy) {
          element.disabled = true;
          element.title = busyMessage;
        } else {
          element.disabled = element.dataset.alphaBusyDefaultDisabled === "1";
          element.title = element.dataset.alphaBusyDefaultTitle || "";
        }
      });
      if (busy) {
        canvas.connecting_node = null;
        canvas.connecting_output = null;
        canvas.connecting_slot = null;
        canvas.dragging_canvas = false;
        canvas.dragging_rectangle = null;
        canvas.last_mouse_dragging = false;
        clearOrphanSearchBoxes();
        if (inspectorPortPicker) setInspectorPortPicker(null);
      }
      graph.setDirtyCanvas(true, true);
    }

    function guardBusyAction(showError = true) {
      const message = busyStatusMessage();
      if (!message) return false;
      if (showError) setStatus(message, true);
      return true;
    }

    function setReloadEnabled() {
      syncBusyUiState();
      const button = root.querySelector("[data-alpha-reload]");
      if (!button) return;
      if (isReloadInFlight()) {
        button.disabled = true;
        button.title = "Reload in progress";
        return;
      }
      if (isAttachInFlight()) {
        button.disabled = true;
        button.title = "Attach in progress";
        return;
      }
      const composerLoadButton = document.getElementById("loadPipelineBtn");
      const blocked = !!composerLoadButton?.disabled;
      button.disabled = blocked;
      button.title = blocked
        ? (composerLoadButton?.title || "No current lane attachment available")
        : "";
    }

    function setAttachEnabled() {
      syncBusyUiState();
      const button = root.querySelector("[data-alpha-attach]");
      if (!button) return;
      const composerAttachButton = document.getElementById("attachPipelineBtn");
      const composerBlocked = !!composerAttachButton?.disabled;
      const composerTitle = composerAttachButton?.title || "";
      const blocked = isReloadInFlight() || isAttachInFlight() || validationState.errors.length > 0 || composerBlocked;
      button.disabled = blocked;
      if (isReloadInFlight()) {
        button.title = "Reload in progress";
        return;
      }
      if (isAttachInFlight()) {
        button.title = "Attach in progress";
        return;
      }
      if (validationState.errors.length) {
        button.title = validationState.errors[0]?.message || "Fix graph errors before attaching";
        return;
      }
      if (composerBlocked) {
        button.title = composerTitle || "Complete pipeline metadata before attaching";
        return;
      }
      button.title = validationState.warnings.length
        ? "Warnings present. Review before attaching."
        : "";
    }

    function schemaValueType(spec = {}) {
      let type = spec.type;
      if (Array.isArray(type)) type = type.find((item) => item && item !== "null") || type[0];
      if (!type && spec.enum?.length) return "string";
      if (!type && spec.items) return "array";
      if (!type && spec.properties) return "object";
      return type || "string";
    }

    function parseInspectorArrayValue(text, spec = {}) {
      const trimmed = String(text || "").trim();
      if (!trimmed) return [];
      const items = trimmed.includes("\n")
        ? trimmed.split("\n")
        : trimmed.split(",").map((item) => item.trim());
      const itemType = schemaValueType(spec.items || {});
      return items
        .map((item) => item.trim())
        .filter(Boolean)
        .map((item) => {
          if (item.startsWith("{") || item.startsWith("[")) {
            try {
              return JSON.parse(item);
            } catch {
              return item;
            }
          }
          if (itemType === "integer") return Number.parseInt(item, 10);
          if (itemType === "number") return Number(item);
          if (itemType === "boolean") return item === "true";
          return item;
        });
    }

    function parseInspectorObjectValue(text) {
      const trimmed = String(text || "").trim();
      if (!trimmed) return {};
      return JSON.parse(trimmed);
    }

    function syncNodeWidgetsFromProperties(node) {
      (node.widgets || []).forEach((widget) => {
        const propertyName = String(widget.name || "").replace(/\s+/g, "");
        const matchingKey = Object.keys(node.properties || {}).find((key) => humanize(key).replace(/\s+/g, "") === propertyName);
        if (matchingKey && Object.prototype.hasOwnProperty.call(node.properties || {}, matchingKey)) {
          widget.value = node.properties[matchingKey];
          return;
        }
        widget.value = widget.type === "toggle" ? false : "";
      });
    }

    function selectedPrimaryNode() {
      const selected = selectedGraphNodes();
      if (!selected.length) return null;
      if (canvas.node_selected && selected.some((node) => node.id === canvas.node_selected.id)) {
        return canvas.node_selected;
      }
      return selected[selected.length - 1];
    }

    function validationIssueForNode(nodeId) {
      return validationState.errors.find((issue) => issue.nodeId === nodeId)
        || validationState.warnings.find((issue) => issue.nodeId === nodeId)
        || null;
    }

    function isGraphOutputNode(node) {
      return nodeByGraphId.get(node?.id)?.moduleId === "graph-output";
    }

    function explorerModeMatchesNode(node) {
      if (inspectorExplorerMode === "issues") return Boolean(validationIssueForNode(node.id));
      if (inspectorExplorerMode === "outputs") return isGraphOutputNode(node);
      return true;
    }

    function describeGraphOutputSummary(node) {
      if (!isGraphOutputNode(node)) return "";
      const valueSlot = typeof node.findInputSlot === "function"
        ? node.findInputSlot("value")
        : 0;
      const input = (node.inputs || [])[valueSlot >= 0 ? valueSlot : 0];
      if (!input || input.link == null) {
        return "unconnected";
      }
      const link = graph.links?.[input.link];
      const sourceNode = link ? graph.getNodeById(link.origin_id) : null;
      const sourcePort = sourceNode?.outputs?.[link.origin_slot];
      if (!sourceNode || !sourcePort) {
        return "unconnected";
      }
      return `${String(node.properties?.dataKey || "").trim() || "output"} <- ${nodeLabel(sourceNode)}.${sourcePort.name || `output_${link.origin_slot}`}`;
    }

    function describeExplorerNodeMeta(node) {
      const meta = nodeByGraphId.get(node?.id);
      if (!meta) return "";
      if (isGraphOutputNode(node)) {
        return describeGraphOutputSummary(node) || [meta.moduleId, meta.instanceId].filter(Boolean).join(" · ");
      }
      return [meta.moduleId, meta.instanceId].filter(Boolean).join(" · ");
    }

    function filteredExplorerNodes() {
      const query = String(inspectorExplorerQuery || "").trim().toLowerCase();
      const nodes = (graph._nodes || [])
        .filter((node) => nodeByGraphId.has(node.id))
        .filter((node) => explorerModeMatchesNode(node))
        .sort((a, b) => (a.pos?.[1] || 0) - (b.pos?.[1] || 0) || (a.pos?.[0] || 0) - (b.pos?.[0] || 0));
      if (!query) return nodes;
      return nodes.filter((node) => {
        const meta = nodeByGraphId.get(node.id);
        const issue = validationIssueForNode(node.id);
        const haystack = [
          nodeLabel(node),
          meta?.moduleId,
          meta?.instanceId,
          isGraphOutputNode(node) ? node.properties?.dataKey : "",
          isGraphOutputNode(node) ? describeGraphOutputSummary(node) : "",
          issue?.message,
        ].map((value) => String(value || "").toLowerCase());
        return haystack.some((value) => value.includes(query));
      });
    }

    function normalizedListIndex(index, count) {
      if (!count) return -1;
      return Math.max(0, Math.min(Number.isFinite(index) ? index : 0, count - 1));
    }

    function selectExplorerNodes(nodes) {
      if (!nodes?.length) return false;
      canvas.selectNodes?.(nodes, false);
      revealNode(nodes[nodes.length - 1]);
      return true;
    }

    function activateExplorerItemAt(index = 0) {
      const buttons = [...(inspectorEl?.querySelectorAll(".alpha-litegraph-explorer-item") || [])];
      const resolvedIndex = normalizedListIndex(index, buttons.length);
      if (resolvedIndex < 0) return false;
      const button = buttons[resolvedIndex];
      if (!button) return false;
      button.click();
      return true;
    }

    function activatePortPickerOptionAt(index = 0) {
      const buttons = [...(inspectorEl?.querySelectorAll(".alpha-litegraph-port-picker-item") || [])];
      const resolvedIndex = normalizedListIndex(index, buttons.length);
      if (resolvedIndex < 0) return false;
      const button = buttons[resolvedIndex];
      if (!button) return false;
      button.click();
      return true;
    }

    function focusExplorerFilter({ select = false } = {}) {
      const input = inspectorEl?.querySelector("[data-alpha-explorer-filter]");
      if (!input || typeof input.focus !== "function") return false;
      input.scrollIntoView({ block: "nearest" });
      input.focus({ preventScroll: true });
      if (select && typeof input.setSelectionRange === "function") {
        const valueLength = String(input.value || "").length;
        input.setSelectionRange(0, valueLength);
      }
      return true;
    }

    function scrollActiveExplorerItemIntoView() {
      const item = inspectorEl?.querySelector(".alpha-litegraph-explorer-item.active");
      if (!item) return false;
      item.scrollIntoView({ block: "nearest" });
      return true;
    }

    function scrollActivePortPickerItemIntoView() {
      const item = inspectorEl?.querySelector(".alpha-litegraph-port-picker-item.active");
      if (!item) return false;
      item.scrollIntoView({ block: "nearest" });
      return true;
    }

    function describeNodeInputs(node) {
      return (node.inputs || []).map((input, inputIndex) => {
        const link = input?.link != null ? graph.links?.[input.link] : null;
        const sourceNode = link ? graph.getNodeById(link.origin_id) : null;
        const sourcePort = sourceNode?.outputs?.[link.origin_slot];
        const sourceMeta = nodeByGraphId.get(sourceNode?.id);
        return {
          name: input?.name || `input_${inputIndex}`,
          type: input?.type || "any",
          connected: Boolean(link && sourceNode),
          sourceNodeId: sourceNode?.id,
          sourceNodeTitle: sourceNode ? nodeLabel(sourceNode) : "",
          sourcePortName: sourcePort?.name || "",
          wire: sourcePort?.name ? sourceMeta?.outputWires?.[sourcePort.name] || "" : "",
        };
      });
    }

    function describeNodeOutputs(node) {
      const meta = nodeByGraphId.get(node.id);
      return (node.outputs || []).map((output, outputIndex) => ({
        name: output?.name || `output_${outputIndex}`,
        outputIndex,
        type: output?.type || "any",
        wire: output?.name ? meta?.outputWires?.[output.name] || "" : "",
        targets: (output?.links || []).map((linkId) => {
          const link = graph.links?.[linkId];
          const targetNode = link ? graph.getNodeById(link.target_id) : null;
          const targetInput = targetNode?.inputs?.[link.target_slot];
          return {
            linkId,
            nodeId: targetNode?.id,
            nodeTitle: targetNode ? nodeLabel(targetNode) : "",
            inputName: targetInput?.name || "",
          };
        }).filter((target) => target.nodeId != null),
      }));
    }

    function searchEventForPort(node, side, slotIndex) {
      const offsetPos = node.getConnectionPos(side === "input", slotIndex);
      const canvasPos = canvas.convertOffsetToCanvas(offsetPos);
      const bounds = canvasEl.getBoundingClientRect();
      return {
        clientX: bounds.left + canvasPos[0],
        clientY: bounds.top + canvasPos[1],
        pageX: bounds.left + canvasPos[0],
        pageY: bounds.top + canvasPos[1],
        layerX: canvasPos[0],
        layerY: canvasPos[1],
      };
    }

    function openInspectorPortSearch(node, side, slotIndex) {
      if (!node) return;
      LiteGraph.LGraphCanvas.active_canvas = canvas;
      revealNode(node);
      const event = searchEventForPort(node, side, slotIndex);
      if (side === "input") {
        const input = (node.inputs || [])[slotIndex];
        if (!input) return;
        canvas.showSearchBox(event, {
          node_to: node,
          slot_from: { name: input.name, slot_index: slotIndex },
          type_filter_out: input.type || "any",
          do_type_filter: true,
          show_all_if_empty: true,
          show_all_on_open: true,
        });
        return;
      }
      const output = (node.outputs || [])[slotIndex];
      if (!output) return;
      canvas.showSearchBox(event, {
        node_from: node,
        slot_from: { name: output.name, slot_index: slotIndex },
        type_filter_in: output.type || "any",
        do_type_filter: true,
        show_all_if_empty: true,
        show_all_on_open: true,
      });
    }

    function disconnectInspectorInput(node, inputIndex) {
      if (!node) return;
      syncCurrentHistoryContext();
      withAutoStructureSyncSuppressed(() => {
        node.disconnectInput(inputIndex);
      });
      graph.setDirtyCanvas(true, true);
      commitGraphStructureChange();
      renderInspector();
    }

    function disconnectInspectorOutputTarget(node, outputIndex, targetNodeId) {
      if (!node) return;
      syncCurrentHistoryContext();
      withAutoStructureSyncSuppressed(() => {
        node.disconnectOutput(outputIndex, targetNodeId);
      });
      graph.setDirtyCanvas(true, true);
      commitGraphStructureChange();
      renderInspector();
    }

    function setInspectorPortPicker(nextPicker) {
      inspectorPortPickerActiveIndex = 0;
      inspectorPortPicker = nextPicker;
      renderInspector();
      if (!nextPicker || !inspectorEl) return;
      window.requestAnimationFrame(() => {
        const filter = [...inspectorEl.querySelectorAll("[data-alpha-port-picker-filter]")]
          .find((input) => input.dataset.alphaPortPickerFilter === nextPicker.side && input.dataset.alphaPortIndex === String(nextPicker.slotIndex)) || null;
        if (!filter) return;
        filter.scrollIntoView({ block: "nearest" });
        filter.focus({ preventScroll: true });
        if (typeof filter.setSelectionRange === "function") {
          const valueLength = String(filter.value || "").length;
          filter.setSelectionRange(valueLength, valueLength);
        }
      });
    }

    function normalizePickerQuery(value) {
      return String(value || "").trim().toLowerCase();
    }

    function filteredPickerOptions(options = []) {
      const query = normalizePickerQuery(inspectorPortPicker?.query || "");
      if (!query) return options;
      return options.filter((option) => {
        const haystack = [
          option.nodeTitle,
          option.moduleId,
          option.instanceId,
          option.slotName,
          option.wire,
          option.type,
        ].map((item) => String(item || "").toLowerCase());
        return haystack.some((item) => item.includes(query));
      });
    }

    function existingSourceOptionsForInput(node, inputIndex) {
      const input = (node.inputs || [])[inputIndex];
      if (!input) return [];
      return (graph._nodes || [])
        .filter((candidate) => candidate && candidate.id !== node.id)
        .flatMap((candidate) => {
          const candidateMeta = nodeByGraphId.get(candidate.id);
          return (candidate.outputs || []).map((output, outputIndex) => ({
            nodeId: candidate.id,
            nodeTitle: nodeLabel(candidate),
            moduleId: candidateMeta?.moduleId || "",
            instanceId: candidateMeta?.instanceId || "",
            slotIndex: outputIndex,
            slotName: output?.name || `output_${outputIndex}`,
            wire: candidateMeta?.outputWires?.[output?.name] || "",
            type: output?.type || "any",
          }));
        })
        .filter((option) => portTypeIsCompatible(option.type, input.type || "any"));
    }

    function existingTargetOptionsForOutput(node, outputIndex) {
      const output = (node.outputs || [])[outputIndex];
      if (!output) return [];
      return (graph._nodes || [])
        .filter((candidate) => candidate && candidate.id !== node.id)
        .flatMap((candidate) => {
          const candidateMeta = nodeByGraphId.get(candidate.id);
          return (candidate.inputs || []).map((input, inputIndex) => ({
            nodeId: candidate.id,
            nodeTitle: nodeLabel(candidate),
            moduleId: candidateMeta?.moduleId || "",
            instanceId: candidateMeta?.instanceId || "",
            slotIndex: inputIndex,
            slotName: input?.name || `input_${inputIndex}`,
            type: input?.type || "any",
          }));
        })
        .filter((option) => portTypeIsCompatible(output.type || "any", option.type));
    }

    function renderExistingSourcePicker(node, inputIndex) {
      const picker = inspectorPortPicker;
      if (!picker || picker.nodeId !== node.id || picker.side !== "input" || picker.slotIndex !== inputIndex) return "";
      const allOptions = existingSourceOptionsForInput(node, inputIndex);
      const options = filteredPickerOptions(allOptions);
      const activeIndex = normalizedListIndex(inspectorPortPickerActiveIndex, options.length);
      inspectorPortPickerActiveIndex = activeIndex < 0 ? 0 : activeIndex;
      return `
        <div class="alpha-litegraph-port-picker">
          <div class="alpha-litegraph-port-picker-title">Use Existing</div>
          <input
            type="text"
            class="alpha-litegraph-port-picker-filter"
            data-alpha-port-picker-filter="input"
            data-alpha-port-index="${escapeHtml(inputIndex)}"
            value="${escapeHtml(picker.query || "")}"
            placeholder="Filter existing outputs"
          />
          ${options.length ? `
            <div class="alpha-litegraph-port-picker-list">
              ${options.map((option) => `
                <button
                  type="button"
                  class="alpha-litegraph-port-picker-item ${activeIndex >= 0 && options[activeIndex] === option ? "active" : ""}"
                  data-alpha-port-action="connect-existing-choice"
                  data-alpha-port-side="input"
                  data-alpha-port-index="${escapeHtml(inputIndex)}"
                  data-alpha-source-node-id="${escapeHtml(option.nodeId)}"
                  data-alpha-source-slot-index="${escapeHtml(option.slotIndex)}"
                >
                  <span class="alpha-litegraph-port-picker-item-title">${escapeHtml(option.nodeTitle)} · ${escapeHtml(option.slotName)}</span>
                  <span class="alpha-litegraph-port-picker-item-meta">${escapeHtml([option.moduleId, option.instanceId, option.wire].filter(Boolean).join(" · "))}</span>
                </button>
              `).join("")}
            </div>
          ` : `<div class="muted">${allOptions.length ? "No matches for current filter" : "No compatible existing outputs"}</div>`}
          <div class="alpha-litegraph-port-actions">
            <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="close-existing-picker">Close</button>
          </div>
        </div>
      `;
    }

    function renderExistingTargetPicker(node, outputIndex) {
      const picker = inspectorPortPicker;
      if (!picker || picker.nodeId !== node.id || picker.side !== "output" || picker.slotIndex !== outputIndex) return "";
      const allOptions = existingTargetOptionsForOutput(node, outputIndex);
      const options = filteredPickerOptions(allOptions);
      const activeIndex = normalizedListIndex(inspectorPortPickerActiveIndex, options.length);
      inspectorPortPickerActiveIndex = activeIndex < 0 ? 0 : activeIndex;
      return `
        <div class="alpha-litegraph-port-picker">
          <div class="alpha-litegraph-port-picker-title">Use Existing</div>
          <input
            type="text"
            class="alpha-litegraph-port-picker-filter"
            data-alpha-port-picker-filter="output"
            data-alpha-port-index="${escapeHtml(outputIndex)}"
            value="${escapeHtml(picker.query || "")}"
            placeholder="Filter existing inputs"
          />
          ${options.length ? `
            <div class="alpha-litegraph-port-picker-list">
              ${options.map((option) => `
                <button
                  type="button"
                  class="alpha-litegraph-port-picker-item ${activeIndex >= 0 && options[activeIndex] === option ? "active" : ""}"
                  data-alpha-port-action="connect-existing-choice"
                  data-alpha-port-side="output"
                  data-alpha-port-index="${escapeHtml(outputIndex)}"
                  data-alpha-target-node-id="${escapeHtml(option.nodeId)}"
                  data-alpha-target-slot-index="${escapeHtml(option.slotIndex)}"
                >
                  <span class="alpha-litegraph-port-picker-item-title">${escapeHtml(option.nodeTitle)} · ${escapeHtml(option.slotName)}</span>
                  <span class="alpha-litegraph-port-picker-item-meta">${escapeHtml([option.moduleId, option.instanceId].filter(Boolean).join(" · "))}</span>
                </button>
              `).join("")}
            </div>
          ` : `<div class="muted">${allOptions.length ? "No matches for current filter" : "No compatible existing inputs"}</div>`}
          <div class="alpha-litegraph-port-actions">
            <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="close-existing-picker">Close</button>
          </div>
        </div>
      `;
    }

    function connectExistingToInput(node, inputIndex, sourceNodeId, sourceSlotIndex) {
      const sourceNode = graph.getNodeById(sourceNodeId);
      if (!node || !sourceNode) return;
      syncCurrentHistoryContext();
      withAutoStructureSyncSuppressed(() => {
        sourceNode.connect(sourceSlotIndex, node, inputIndex);
      });
      setInspectorPortPicker(null);
      graph.setDirtyCanvas(true, true);
      commitGraphStructureChange();
      renderInspector();
    }

    function connectOutputToExisting(node, outputIndex, targetNodeId, targetSlotIndex) {
      const targetNode = graph.getNodeById(targetNodeId);
      if (!node || !targetNode) return;
      syncCurrentHistoryContext();
      withAutoStructureSyncSuppressed(() => {
        node.connect(outputIndex, targetNode, targetSlotIndex);
      });
      setInspectorPortPicker(null);
      graph.setDirtyCanvas(true, true);
      commitGraphStructureChange();
      renderInspector();
    }

    function traverseGraphNodes(startNode, direction) {
      if (!startNode) return [];
      const visited = new Set();
      const ordered = [];
      const queue = [startNode];
      while (queue.length) {
        const node = queue.shift();
        if (!node || visited.has(node.id)) continue;
        visited.add(node.id);
        ordered.push(node);
        if (direction === "upstream") {
          (node.inputs || []).forEach((input) => {
            if (input?.link == null) return;
            const link = graph.links?.[input.link];
            const sourceNode = link ? graph.getNodeById(link.origin_id) : null;
            if (sourceNode && !visited.has(sourceNode.id)) queue.push(sourceNode);
          });
          continue;
        }
        (node.outputs || []).forEach((output) => {
          (output?.links || []).forEach((linkId) => {
            const link = graph.links?.[linkId];
            const targetNode = link ? graph.getNodeById(link.target_id) : null;
            if (targetNode && !visited.has(targetNode.id)) queue.push(targetNode);
          });
        });
      }
      return ordered;
    }

    function selectNodeChain(node, direction) {
      const chain = traverseGraphNodes(node, direction);
      if (!chain.length) return false;
      canvas.selectNodes?.(chain, false);
      revealNode(node);
      return true;
    }

    function frameNodes(nodes, options = {}) {
      if (!nodes?.length) return false;
      const padding = options.padding ?? 96;
      const minScale = options.minScale ?? 0.35;
      const maxScale = options.maxScale ?? 1.4;
      const persistViewport = options.persistViewport !== false;
      const canvasRect = canvasEl.getBoundingClientRect();
      const bounds = nodes.reduce((acc, node) => {
        const x = node.pos?.[0] || 0;
        const y = node.pos?.[1] || 0;
        const w = node.size?.[0] || 292;
        const h = node.size?.[1] || 148;
        acc.minX = Math.min(acc.minX, x);
        acc.minY = Math.min(acc.minY, y);
        acc.maxX = Math.max(acc.maxX, x + w);
        acc.maxY = Math.max(acc.maxY, y + h);
        return acc;
      }, {
        minX: Number.POSITIVE_INFINITY,
        minY: Number.POSITIVE_INFINITY,
        maxX: Number.NEGATIVE_INFINITY,
        maxY: Number.NEGATIVE_INFINITY,
      });
      const graphWidth = Math.max(bounds.maxX - bounds.minX, 1);
      const graphHeight = Math.max(bounds.maxY - bounds.minY, 1);
      const width = canvasRect.width || canvasEl.clientWidth || stageEl.clientWidth || canvasEl.width || 1200;
      const height = canvasRect.height || canvasEl.clientHeight || stageEl.clientHeight || canvasEl.height || 720;
      const scaleX = (width - padding * 2) / graphWidth;
      const scaleY = (height - padding * 2) / graphHeight;
      const scale = Math.max(minScale, Math.min(maxScale, scaleX, scaleY));
      const offsetX = ((width - graphWidth * scale) * 0.5) - bounds.minX * scale;
      const offsetY = ((height - graphHeight * scale) * 0.5) - bounds.minY * scale;
      canvas.ds.scale = scale;
      canvas.ds.offset[0] = offsetX;
      canvas.ds.offset[1] = offsetY;
      graph.setDirtyCanvas(true, true);
      if (persistViewport) scheduleViewportPersist();
      return true;
    }

    function arrangeNodeSubset(nodes) {
      syncCurrentHistoryContext();
      const subset = (nodes || []).filter((node) => node?.graph === graph);
      if (!subset.length) return false;
      const originX = Math.min(...subset.map((node) => node.pos?.[0] || 0));
      const originY = Math.min(...subset.map((node) => node.pos?.[1] || 0));
      const selectedIds = new Set(subset.map((node) => node.id));
      const inDegree = new Map();
      const adjacency = new Map();
      subset.forEach((node) => {
        inDegree.set(node.id, 0);
        adjacency.set(node.id, []);
      });
      Object.values(graph.links).forEach((link) => {
        if (!selectedIds.has(link.origin_id) || !selectedIds.has(link.target_id)) return;
        inDegree.set(link.target_id, (inDegree.get(link.target_id) || 0) + 1);
        adjacency.get(link.origin_id)?.push(link.target_id);
      });
      const queue = subset
        .filter((node) => (inDegree.get(node.id) || 0) === 0)
        .sort((a, b) => (a.pos?.[1] || 0) - (b.pos?.[1] || 0) || (a.pos?.[0] || 0) - (b.pos?.[0] || 0));
      const ordered = [];
      const visited = new Set();
      while (queue.length) {
        const node = queue.shift();
        if (!node || visited.has(node.id)) continue;
        visited.add(node.id);
        ordered.push(node);
        (adjacency.get(node.id) || []).forEach((targetId) => {
          inDegree.set(targetId, (inDegree.get(targetId) || 1) - 1);
          if ((inDegree.get(targetId) || 0) === 0) {
            const target = graph.getNodeById(targetId);
            if (target && !visited.has(target.id)) queue.push(target);
          }
        });
      }
      subset
        .filter((node) => !visited.has(node.id))
        .sort((a, b) => (a.pos?.[1] || 0) - (b.pos?.[1] || 0) || (a.pos?.[0] || 0) - (b.pos?.[0] || 0))
        .forEach((node) => ordered.push(node));
      const layers = [];
      const layerById = new Map();
      ordered.forEach((node) => {
        const parentLayers = (node.inputs || [])
          .map((input) => {
            const link = input?.link != null ? graph.links?.[input.link] : null;
            if (!link || !selectedIds.has(link.origin_id)) return null;
            return layerById.get(link.origin_id);
          })
          .filter((value) => Number.isInteger(value));
        const layer = parentLayers.length ? Math.max(...parentLayers) + 1 : 0;
        layerById.set(node.id, layer);
        layers[layer] ||= [];
        layers[layer].push(node);
      });
      layers.forEach((layer, layerIndex) => {
        layer.forEach((node, rowIndex) => {
          node.pos = [originX + layerIndex * 420, originY + rowIndex * 220];
        });
      });
      graph.setDirtyCanvas(true, true);
      commitNodePositionChange();
      return true;
    }

    function renderInputInspectorRows(node) {
      const rows = describeNodeInputs(node);
      if (!rows.length) return '<div class="muted">No input ports</div>';
      return rows.map((row, inputIndex) => `
        <div class="alpha-litegraph-port-row ${row.connected ? "connected" : "empty"}">
          <div class="alpha-litegraph-port-head">
            <strong>${escapeHtml(row.name)}</strong>
            <span class="alpha-litegraph-port-type">${escapeHtml(row.type)}</span>
          </div>
          ${row.connected ? `
            <div class="alpha-litegraph-port-link">
              <span class="muted">From</span>
              <button type="button" class="alpha-litegraph-port-node" data-alpha-node-id="${escapeHtml(row.sourceNodeId)}">${escapeHtml(row.sourceNodeTitle)}</button>
              <span class="alpha-litegraph-port-wire">${escapeHtml(row.sourcePortName)}${row.wire ? ` · ${row.wire}` : ""}</span>
            </div>
            <div class="alpha-litegraph-port-actions">
              <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="replace" data-alpha-port-side="input" data-alpha-port-index="${escapeHtml(inputIndex)}">Replace</button>
              <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="open-existing-picker" data-alpha-port-side="input" data-alpha-port-index="${escapeHtml(inputIndex)}">Use Existing</button>
              <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="disconnect" data-alpha-port-side="input" data-alpha-port-index="${escapeHtml(inputIndex)}">Disconnect</button>
            </div>
          ` : '<div class="muted">Unconnected</div>'}
          ${!row.connected ? `<div class="alpha-litegraph-port-actions"><button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="connect" data-alpha-port-side="input" data-alpha-port-index="${escapeHtml(inputIndex)}">Connect</button><button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="open-existing-picker" data-alpha-port-side="input" data-alpha-port-index="${escapeHtml(inputIndex)}">Use Existing</button></div>` : ""}
          ${renderExistingSourcePicker(node, inputIndex)}
        </div>
      `).join("");
    }

    function renderOutputInspectorRows(node) {
      const rows = describeNodeOutputs(node);
      if (!rows.length) return '<div class="muted">No output ports</div>';
      return rows.map((row, outputIndex) => `
        <div class="alpha-litegraph-port-row ${row.targets.length ? "connected" : "empty"}">
          <div class="alpha-litegraph-port-head">
            <strong>${escapeHtml(row.name)}</strong>
            <span class="alpha-litegraph-port-type">${escapeHtml(row.type)}</span>
          </div>
          ${row.wire ? `<div class="alpha-litegraph-port-wire">${escapeHtml(row.wire)}</div>` : ""}
          <div class="alpha-litegraph-port-actions">
            <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="connect" data-alpha-port-side="output" data-alpha-port-index="${escapeHtml(outputIndex)}">Add Downstream</button>
            <button type="button" class="alpha-litegraph-port-action" data-alpha-port-action="open-existing-picker" data-alpha-port-side="output" data-alpha-port-index="${escapeHtml(outputIndex)}">Use Existing</button>
          </div>
          ${row.targets.length ? `
            <div class="alpha-litegraph-port-targets">
              ${row.targets.map((target) => `
                <span class="alpha-litegraph-port-target-chip">
                  <button type="button" class="alpha-litegraph-port-node" data-alpha-node-id="${escapeHtml(target.nodeId)}">
                    ${escapeHtml(target.nodeTitle)}${target.inputName ? ` · ${escapeHtml(target.inputName)}` : ""}
                  </button>
                  <button
                    type="button"
                    class="alpha-litegraph-port-action alpha-litegraph-port-action-danger"
                    data-alpha-port-action="disconnect-output-target"
                    data-alpha-port-side="output"
                    data-alpha-port-index="${escapeHtml(outputIndex)}"
                    data-alpha-target-node-id="${escapeHtml(target.nodeId)}"
                  >
                    Disconnect
                  </button>
                </span>
              `).join("")}
            </div>
          ` : '<div class="muted">No downstream consumers</div>'}
          ${renderExistingTargetPicker(node, outputIndex)}
        </div>
      `).join("");
    }

    function captureInspectorFocusState() {
      const active = document.activeElement;
      if (!inspectorEl || !active || !inspectorEl.contains(active)) return null;
      if (active.matches?.("[data-alpha-explorer-filter]")) {
        return {
          kind: "explorer-filter",
          selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
          selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }
      if (active.matches?.("[data-alpha-port-picker-filter]")) {
        return {
          kind: "port-picker-filter",
          side: active.dataset.alphaPortPickerFilter || "",
          slotIndex: active.dataset.alphaPortIndex || "",
          selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
          selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }
      return null;
    }

    function restoreInspectorFocusState(state) {
      if (!state || !inspectorEl) return;
      let target = null;
      if (state.kind === "explorer-filter") {
        target = inspectorEl.querySelector("[data-alpha-explorer-filter]");
      } else if (state.kind === "port-picker-filter") {
        target = [...inspectorEl.querySelectorAll("[data-alpha-port-picker-filter]")]
          .find((input) => input.dataset.alphaPortPickerFilter === state.side && input.dataset.alphaPortIndex === state.slotIndex) || null;
      }
      if (!target || typeof target.focus !== "function") return;
      target.scrollIntoView({ block: "nearest" });
      target.focus({ preventScroll: true });
      if (typeof target.setSelectionRange === "function"
        && typeof state.selectionStart === "number"
        && typeof state.selectionEnd === "number") {
        target.setSelectionRange(state.selectionStart, state.selectionEnd);
      }
      if (state.kind === "explorer-filter") {
        scrollActiveExplorerItemIntoView();
      } else if (state.kind === "port-picker-filter") {
        scrollActivePortPickerItemIntoView();
      }
    }

    function renderInspectorEmpty() {
      if (!inspectorEl) return;
      const nodes = filteredExplorerNodes();
      const issueCount = (graph._nodes || []).filter((node) => validationIssueForNode(node.id)).length;
      const outputCount = (graph._nodes || []).filter((node) => isGraphOutputNode(node)).length;
      const activeIndex = normalizedListIndex(inspectorExplorerActiveIndex, nodes.length);
      inspectorExplorerActiveIndex = activeIndex < 0 ? 0 : activeIndex;
      inspectorEl.innerHTML = `
        <div class="alpha-litegraph-inspector-card alpha-litegraph-inspector-empty">
          <div class="alpha-litegraph-inspector-head">
            <div class="alpha-litegraph-inspector-title">Node Explorer</div>
            <div class="muted">Filter and jump to existing graph nodes.</div>
          </div>
          <div class="alpha-litegraph-explorer-mode">
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "all" ? "active" : ""}" data-alpha-explorer-mode="all">All</button>
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "outputs" ? "active" : ""}" data-alpha-explorer-mode="outputs">Outputs${outputCount ? ` (${outputCount})` : ""}</button>
            <button type="button" class="alpha-litegraph-explorer-mode-btn ${inspectorExplorerMode === "issues" ? "active" : ""}" data-alpha-explorer-mode="issues">Issues${issueCount ? ` (${issueCount})` : ""}</button>
          </div>
          <div class="alpha-litegraph-explorer-actions">
            <button type="button" class="alpha-litegraph-explorer-action" data-alpha-explorer-action="select-filtered" ${nodes.length ? "" : "disabled"}>Select Filtered${nodes.length ? ` (${nodes.length})` : ""}</button>
            <button type="button" class="alpha-litegraph-explorer-action" data-alpha-explorer-action="arrange-filtered" ${nodes.length ? "" : "disabled"}>Arrange Filtered</button>
            <button type="button" class="alpha-litegraph-explorer-action" data-alpha-explorer-action="frame-filtered" ${nodes.length ? "" : "disabled"}>Frame Filtered</button>
          </div>
          <input
            type="text"
            class="alpha-litegraph-explorer-filter"
            data-alpha-explorer-filter
            value="${escapeHtml(inspectorExplorerQuery)}"
            placeholder="Find by title, module, instance, output, or issue"
          />
          <div class="alpha-litegraph-explorer-list">
            ${nodes.length ? nodes.map((node) => {
              const issue = validationIssueForNode(node.id);
              const metaText = describeExplorerNodeMeta(node);
              return `
                <button type="button" class="alpha-litegraph-explorer-item ${activeIndex >= 0 && nodes[activeIndex] === node ? "active" : ""}" data-alpha-node-id="${escapeHtml(node.id)}">
                  <span class="alpha-litegraph-explorer-item-title">${escapeHtml(nodeLabel(node))}</span>
                  <span class="alpha-litegraph-explorer-item-meta">${escapeHtml(metaText)}</span>
                  ${issue ? `<span class="alpha-litegraph-explorer-item-issue ${escapeHtml(issue.level || "warning")}">${escapeHtml(issue.message)}</span>` : ""}
                </button>
              `;
            }).join("") : `<div class="muted">${inspectorExplorerMode === "issues" ? "No issue nodes match the current filter." : inspectorExplorerMode === "outputs" ? "No Graph Output nodes match the current filter." : "No nodes match the current filter."}</div>`}
          </div>
        </div>
      `;
    }

    function renderInspectorSelectionSummary(selected) {
      if (!inspectorEl) return;
      inspectorEl.innerHTML = `
        <div class="alpha-litegraph-inspector-card">
          <div class="alpha-litegraph-inspector-head">
            <div class="alpha-litegraph-inspector-title">${escapeHtml(`${selected.length} nodes selected`)}</div>
            <div class="muted">Use bulk actions, or pick one node for full config editing.</div>
          </div>
          <div class="alpha-litegraph-inspector-actions">
            <button type="button" data-alpha-inspector-action="copy">Copy</button>
            <button type="button" data-alpha-inspector-action="cut">Cut</button>
            <button type="button" data-alpha-inspector-action="duplicate">Duplicate</button>
            <button type="button" data-alpha-inspector-action="delete">Delete</button>
            <button type="button" data-alpha-inspector-action="arrange-selection">Arrange</button>
            <button type="button" data-alpha-inspector-action="frame-selection">Frame</button>
          </div>
          <div class="alpha-litegraph-inspector-list">
            ${selected.map((node) => `<button type="button" class="alpha-litegraph-inspector-list-item" data-alpha-node-id="${escapeHtml(node.id)}">${escapeHtml(nodeLabel(node))}</button>`).join("")}
          </div>
        </div>
      `;
    }

    function parseInspectorFieldValue(input, spec = {}) {
      const type = input.dataset.schemaType || schemaValueType(spec);
      if (type === "boolean") return !!input.checked;
      if (type === "integer") return input.value === "" ? undefined : Number.parseInt(input.value, 10);
      if (type === "number") return input.value === "" ? undefined : Number(input.value);
      if (type === "array") return parseInspectorArrayValue(input.value, spec);
      if (type === "object") return parseInspectorObjectValue(input.value);
      if (type === "enum") return input.value;
      return input.value === "" ? undefined : input.value;
    }

    function commitInspectorField(node, module, input, { recordHistory = true } = {}) {
      if (!node || !module || !input) return;
      const fieldName = input.dataset.schemaField;
      if (!fieldName) return;
      const spec = module.configSchema?.properties?.[fieldName] || {};
      const messageEl = inspectorEl?.querySelector("[data-alpha-inspector-message]");
      try {
        const value = parseInspectorFieldValue(input, spec);
        node.properties ||= {};
        if (value === undefined) delete node.properties[fieldName];
        else node.properties[fieldName] = value;
        if (module.moduleId === "graph-output" && fieldName === "dataKey") {
          const nextDataKey = String(node.properties?.dataKey || "").trim();
          node.title = nextDataKey ? `Output: ${nextDataKey}` : "Graph Output";
          inspectorEl?.querySelector("[data-alpha-inspector-title]")?.replaceChildren(document.createTextNode(node.title));
        }
        if (messageEl) {
          messageEl.textContent = "";
          messageEl.hidden = true;
        }
        syncNodeWidgetsFromProperties(node);
        graph.setDirtyCanvas(true, true);
        queueEmit({ recordHistory });
      } catch (error) {
        if (messageEl) {
          messageEl.textContent = error?.message || "Invalid field value";
          messageEl.hidden = false;
        }
      }
    }

    function renderSingleNodeInspector(node) {
      if (!inspectorEl || !node) return;
      const meta = nodeByGraphId.get(node.id);
      const module = moduleById.get(meta?.moduleId || "");
      if (!meta || !module) {
        renderInspectorEmpty();
        return;
      }
      const issue = validationIssueForNode(node.id);
      inspectorEl.innerHTML = `
        <div class="alpha-litegraph-inspector-card">
          <div class="alpha-litegraph-inspector-head">
            <div class="alpha-litegraph-inspector-title" data-alpha-inspector-title>${escapeHtml(nodeLabel(node))}</div>
            <div class="muted">${escapeHtml(meta.moduleId)}</div>
          </div>
          <div class="alpha-litegraph-inspector-kv">
            <div><span class="muted">Instance</span><strong>${escapeHtml(meta.instanceId)}</strong></div>
            <div><span class="muted">Inputs</span><strong>${escapeHtml(String((node.inputs || []).length))}</strong></div>
            <div><span class="muted">Outputs</span><strong>${escapeHtml(String((node.outputs || []).length))}</strong></div>
          </div>
          ${issue ? `<div class="alpha-litegraph-inspector-issue ${escapeHtml(issue.level || "warning")}">${escapeHtml(issue.message)}</div>` : ""}
          <div class="alpha-litegraph-inspector-actions">
            <button type="button" data-alpha-inspector-action="copy">Copy</button>
            <button type="button" data-alpha-inspector-action="duplicate">Duplicate</button>
            <button type="button" data-alpha-inspector-action="delete">Delete</button>
            <button type="button" data-alpha-inspector-action="select-upstream">Select Upstream</button>
            <button type="button" data-alpha-inspector-action="select-downstream">Select Downstream</button>
          </div>
          <div class="alpha-litegraph-inspector-section">
            <div class="alpha-litegraph-inspector-section-title">Inputs</div>
            <div class="alpha-litegraph-port-list">${renderInputInspectorRows(node)}</div>
          </div>
          <div class="alpha-litegraph-inspector-section">
            <div class="alpha-litegraph-inspector-section-title">Outputs</div>
            <div class="alpha-litegraph-port-list">${renderOutputInspectorRows(node)}</div>
          </div>
          <div class="alpha-litegraph-inspector-section">
            <div class="alpha-litegraph-inspector-section-title">Config</div>
            <div data-alpha-inspector-message class="alpha-litegraph-inspector-message" hidden></div>
            <div data-alpha-inspector-fields class="structured-fields structured-fields-inline"></div>
          </div>
        </div>
      `;
      const fieldsEl = inspectorEl.querySelector("[data-alpha-inspector-fields]");
      forms.renderSchemaFields(fieldsEl, module.configSchema || {}, node.properties || {});
      fieldsEl.querySelectorAll("[data-schema-field]").forEach((input) => {
        const schemaType = input.dataset.schemaType || "string";
        const useChangeOnly = schemaType === "array" || schemaType === "object" || input.tagName === "SELECT" || input.type === "checkbox";
        const handler = (event) => {
          if (useChangeOnly && event.type !== "change") return;
          commitInspectorField(node, module, input, {
            recordHistory: useChangeOnly || event.type !== "input",
          });
        };
        input.addEventListener("input", handler);
        input.addEventListener("change", handler);
      });
    }

    function renderInspector() {
      const previousScrollTop = inspectorEl?.scrollTop || 0;
      const focusState = captureInspectorFocusState();
      const selected = selectedGraphNodes();
      if (!selected.length) {
        renderInspectorEmpty();
        if (inspectorEl) inspectorEl.scrollTop = previousScrollTop;
        restoreInspectorFocusState(focusState);
        syncBusyUiState();
        return;
      }
      if (selected.length > 1) {
        renderInspectorSelectionSummary(selected);
        if (inspectorEl) inspectorEl.scrollTop = previousScrollTop;
        restoreInspectorFocusState(focusState);
        syncBusyUiState();
        return;
      }
      renderSingleNodeInspector(selectedPrimaryNode());
      if (inspectorEl) inspectorEl.scrollTop = previousScrollTop;
      restoreInspectorFocusState(focusState);
      syncBusyUiState();
    }

    function nodeLabel(node) {
      return String(node?.title || nodeByGraphId.get(node?.id)?.moduleId || "Node");
    }

    function traceReachableUpstream(node, visited = new Set()) {
      if (!node || visited.has(node.id)) return visited;
      visited.add(node.id);
      (node.inputs || []).forEach((input) => {
        if (input?.link == null) return;
        const link = graph.links?.[input.link];
        const sourceNode = link ? graph.getNodeById(link.origin_id) : null;
        if (sourceNode) traceReachableUpstream(sourceNode, visited);
      });
      return visited;
    }

    function buildValidationState() {
      const errors = [];
      const warnings = [];
      const graphNodes = (graph._nodes || []).filter((node) => nodeByGraphId.has(node.id));
      const outputNodes = graphNodes.filter((node) => nodeByGraphId.get(node.id)?.moduleId === "graph-output");
      const outputNodesByKey = new Map();
      const reachable = new Set();
      let exportedOutputCount = 0;

      outputNodes.forEach((node) => {
        const dataKey = String(node.properties?.dataKey || "").trim();
        if (!dataKey) {
          errors.push({
            level: "error",
            nodeId: node.id,
            message: `${nodeLabel(node)} needs a dataKey`,
          });
        } else {
          outputNodesByKey.set(dataKey, [...(outputNodesByKey.get(dataKey) || []), node]);
        }

        const valueSlot = typeof node.findInputSlot === "function"
          ? node.findInputSlot("value")
          : 0;
        const valueInput = (node.inputs || [])[valueSlot >= 0 ? valueSlot : 0];
        if (valueInput?.link == null) {
          errors.push({
            level: "error",
            nodeId: node.id,
            message: `${dataKey ? `Output ${dataKey}` : nodeLabel(node)} is not connected`,
          });
          reachable.add(node.id);
          return;
        }
        exportedOutputCount += 1;
        traceReachableUpstream(node, reachable);
      });

      graphNodes.forEach((node) => {
        const meta = nodeByGraphId.get(node.id);
        if (!meta || meta.moduleId === "graph-output") return;
        Object.keys(meta.module.ports?.outputs || {}).forEach((portName, outputIndex) => {
          const outputSlot = (node.outputs || [])[outputIndex];
          const hasDownstream = Boolean((outputSlot?.links || []).some((linkId) => graph.links?.[linkId]));
          if (hasDownstream) return;
          const wire = String(state.instances?.[meta.instanceId]?.outputs?.[portName] || meta.outputWires?.[portName] || "").trim();
          if (!wire || wire === defaultNullWire()) return;
          exportedOutputCount += 1;
          traceReachableUpstream(node, reachable);
        });
      });

      outputNodesByKey.forEach((nodes, dataKey) => {
        if (nodes.length < 2) return;
        errors.push({
          level: "error",
          nodeId: nodes[0]?.id,
          message: `Output ${dataKey} is used by ${nodes.length} Graph Output nodes`,
        });
      });

      if (graphNodes.length && !exportedOutputCount) {
        errors.push({
          level: "error",
          nodeId: outputNodes[0]?.id || graphNodes[0]?.id,
          message: "Graph exposes no outputs",
        });
      }

      if (exportedOutputCount) {
        graphNodes.forEach((node) => {
          const meta = nodeByGraphId.get(node.id);
          if (!meta || reachable.has(node.id) || meta.moduleId === "graph-output") return;
          warnings.push({
            level: "warning",
            nodeId: node.id,
            message: `${nodeLabel(node)} does not feed any exposed output`,
          });
        });
      }

      return { errors, warnings };
    }

    function renderValidationState() {
      if (!validationEl) return;
      const { errors, warnings } = validationState;
      const level = errors.length ? "error" : warnings.length ? "warning" : "ok";
      const summary = errors.length
        ? `${errors.length} error${errors.length === 1 ? "" : "s"} blocking attach`
        : warnings.length
          ? `${warnings.length} warning${warnings.length === 1 ? "" : "s"}`
          : "Ready to attach";
      validationEl.className = `alpha-litegraph-validation ${level}`;
      validationEl.innerHTML = [
        `<span class="alpha-litegraph-validation-summary">${escapeHtml(summary)}</span>`,
        ...errors.map((issue) => {
          const attr = issue.nodeId != null ? ` data-alpha-node-id="${escapeHtml(issue.nodeId)}"` : "";
          return `<button type="button" class="alpha-litegraph-validation-pill error"${attr}>${escapeHtml(issue.message)}</button>`;
        }),
        ...warnings.map((issue) => {
          const attr = issue.nodeId != null ? ` data-alpha-node-id="${escapeHtml(issue.nodeId)}"` : "";
          return `<button type="button" class="alpha-litegraph-validation-pill warning"${attr}>${escapeHtml(issue.message)}</button>`;
        }),
      ].join("");
      updateFixedLayoutMetrics();
      setAttachEnabled();
      setReloadEnabled();
    }

    function refreshValidation() {
      validationState = buildValidationState();
      root.__validationState = cloneJson(validationState);
      renderValidationState();
    }

    function emitMetaChange({ recordHistory = true } = {}) {
      ensureHistoryBaseline();
      const nextMeta = currentMetaState();
      setStatus("Unsaved changes");
      onMetaChange?.(nextMeta);
      scheduleViewportPersist(null, nextMeta);
      if (recordHistory && historyInitialized && !historyApplying && !suppressHistoryRecording) {
        recordHistoryEntry(historyEntryFromSnapshot(root.__liteGraphLastSnapshot || null));
      }
    }

    registerModuleTypes(graphModules, (...args) => {
      if (suppressEmit || isBlueprintBusy()) return;
      queueEmit(...args);
    });
    const previousValidConnection = LiteGraph.isValidConnection;
    LiteGraph.isValidConnection = function patchedValidConnection(typeA, typeB) {
      return portTypeIsCompatible(typeA, typeB) || previousValidConnection.call(this, typeA, typeB);
    };

    function resizeCanvas() {
      const width = Math.max(stageEl.clientWidth || 1200, 900);
      const height = Math.max(stageEl.clientHeight || 720, 640);
      canvasEl.width = width;
      canvasEl.height = height;
      canvas.resize(width, height);
      graph.setDirtyCanvas(true, true);
    }

    function createDraftInstance(module, instanceId, configOverride, outputsOverride) {
      const outputs = outputsOverride ? { ...outputsOverride } : {};
      if (!outputsOverride) {
        Object.keys(module.ports?.outputs || {}).forEach((portName) => {
          outputs[portName] = uniqueWireName(state.instances, defaultOutputKey(instanceId, portName));
        });
      }
      const config = { ...forms.schemaDefaults(module.configSchema || {}), ...(configOverride || {}) };
      if (module.moduleId === "graph-output" && !String(config.dataKey || "").trim()) {
        config.dataKey = defaultGraphOutputDataKey();
      }
      return {
        instanceId,
        kind: module.kind,
        moduleId: module.moduleId,
        version: module.version,
        config,
        inputs: Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [name, defaultNullWire()])),
        outputs,
      };
    }

    function duplicateConfigForModule(module, config = {}) {
      const nextConfig = { ...(config || {}) };
      if (module?.moduleId === "graph-output") {
        nextConfig.dataKey = uniqueDataKey(state, String(nextConfig.dataKey || "").trim() || "output_live");
      }
      return nextConfig;
    }

    function selectedGraphNodes() {
      return Object.values(canvas.selected_nodes || {}).filter((node) => node?.graph === graph);
    }

    function serializeGraphNodes(selected) {
      if (!selected?.length) return null;
      const selectedIds = new Set(selected.map((node) => node.id));
      const minX = Math.min(...selected.map((node) => node.pos?.[0] || 0));
      const minY = Math.min(...selected.map((node) => node.pos?.[1] || 0));
      const maxX = Math.max(...selected.map((node) => (node.pos?.[0] || 0) + (node.size?.[0] || 292)));
      const maxY = Math.max(...selected.map((node) => (node.pos?.[1] || 0) + (node.size?.[1] || 148)));
      const nodeEntries = selected
        .sort((a, b) => (a.pos?.[1] || 0) - (b.pos?.[1] || 0) || (a.pos?.[0] || 0) - (b.pos?.[0] || 0))
        .map((node) => {
          const meta = nodeByGraphId.get(node.id);
          if (!meta?.moduleId) return null;
          return {
            sourceNodeId: node.id,
            moduleId: meta.moduleId,
            config: cloneJson(node.properties || {}),
            relativePos: [
              (node.pos?.[0] || 0) - minX,
              (node.pos?.[1] || 0) - minY,
            ],
          };
        })
        .filter(Boolean);
      const connections = [];
      selected.forEach((targetNode) => {
        (targetNode.inputs || []).forEach((input, inputIndex) => {
          if (input?.link == null) return;
          const link = graph.links[input.link];
          if (!link || !selectedIds.has(link.origin_id)) return;
          const sourceNode = graph.getNodeById(link.origin_id);
          const sourceOutput = sourceNode?.outputs?.[link.origin_slot];
          connections.push({
            fromNodeId: link.origin_id,
            fromSlotName: sourceOutput?.name || null,
            toNodeId: targetNode.id,
            toSlotName: input?.name || null,
            toSlotIndex: inputIndex,
          });
        });
      });
      return {
        nodes: nodeEntries,
        connections,
        bounds: {
          width: Math.max(maxX - minX, 292),
          height: Math.max(maxY - minY, 148),
        },
        origin: {
          x: minX,
          y: minY,
        },
      };
    }

    function selectAllGraphNodes() {
      const nodes = (graph._nodes || []).filter((node) => node?.graph === graph);
      if (!nodes.length) return false;
      canvas.selectNodes?.(nodes, false);
      revealNode(nodes[nodes.length - 1]);
      return true;
    }

    function copySelectedNodes() {
      const payload = serializeGraphNodes(selectedGraphNodes());
      if (!payload) return false;
      clipboardData = payload;
      clipboardPasteCount = 0;
      return true;
    }

    function pasteClipboardData(data, options = {}) {
      if (!data?.nodes?.length) return false;
      ensureHistoryBaseline();
      syncCurrentHistoryContext();
      const {
        basePos = null,
        bumpPasteCount = true,
      } = options;
      let resolvedBasePos = basePos;
      if (!resolvedBasePos) {
        const center = viewportCenterGraphPos();
        const offsetX = clipboardPasteCount * 36;
        const offsetY = clipboardPasteCount * 28;
        if (bumpPasteCount) clipboardPasteCount += 1;
        resolvedBasePos = [
          center[0] - data.bounds.width * 0.5 + offsetX,
          center[1] - data.bounds.height * 0.5 + offsetY,
        ];
      }
      const createdBySourceNodeId = new Map();
      const previousSuppressEmit = suppressEmit;
      suppressEmit = true;
      data.nodes.forEach((entry) => {
        const module = moduleById.get(entry.moduleId);
        if (!module) return;
        const created = addNode(entry.moduleId, {
          configOverride: duplicateConfigForModule(module, entry.config || {}),
          preferredPos: [
            resolvedBasePos[0] + (entry.relativePos?.[0] || 0),
            resolvedBasePos[1] + (entry.relativePos?.[1] || 0),
          ],
          focus: false,
          commitHistory: false,
          syncHistory: false,
        });
        if (created) createdBySourceNodeId.set(entry.sourceNodeId, created);
      });
      withAutoStructureSyncSuppressed(() => {
        data.connections.forEach((connection) => {
          const fromNode = createdBySourceNodeId.get(connection.fromNodeId);
          const toNode = createdBySourceNodeId.get(connection.toNodeId);
          if (!fromNode || !toNode) return;
          const fromSlot = connection.fromSlotName ? fromNode.findOutputSlot(connection.fromSlotName) : -1;
          const toSlot = connection.toSlotName ? toNode.findInputSlot(connection.toSlotName) : connection.toSlotIndex;
          if (fromSlot >= 0 && toSlot >= 0) {
            fromNode.connect(fromSlot, toNode, toSlot);
          }
        });
      });
      suppressEmit = previousSuppressEmit;
      const pastedNodes = [...createdBySourceNodeId.values()];
      if (!pastedNodes.length) return false;
      canvas.selectNodes?.(pastedNodes, false);
      revealNode(pastedNodes[pastedNodes.length - 1]);
      commitGraphStructureChange();
      return true;
    }

    function pasteClipboard() {
      return pasteClipboardData(clipboardData, { bumpPasteCount: true });
    }

    function deleteSelectedNodes() {
      ensureHistoryBaseline();
      syncCurrentHistoryContext();
      const selected = selectedGraphNodes();
      if (!selected.length) return false;
      withAutoStructureSyncSuppressed(() => {
        selected.forEach((node) => {
          if (node?.graph === graph) graph.remove(node);
        });
      });
      commitGraphStructureChange();
      return true;
    }

    function cutSelectedNodes() {
      if (!copySelectedNodes()) return false;
      return deleteSelectedNodes();
    }

    function trackGraphNode(node, instance) {
      const module = moduleById.get(instance.moduleId) || moduleByType.get(node.type);
      if (!module || nodeByGraphId.has(node.id)) return;
      state.instances[instance.instanceId] = {
        ...instance,
        config: { ...(instance.config || {}) },
        inputs: { ...(instance.inputs || {}) },
        outputs: { ...(instance.outputs || {}) },
      };
      if (!state.nodeIds.includes(instance.instanceId)) state.nodeIds.push(instance.instanceId);
      nodeByGraphId.set(node.id, {
        instanceId: instance.instanceId,
        kind: instance.kind,
        moduleId: instance.moduleId,
        version: instance.version,
        module,
        outputWires: { ...(instance.outputs || {}) },
      });
    }

    function createGraphNode(instance, index) {
      const module = moduleById.get(instance.moduleId);
      if (!module) return null;
      const palette = modulePalette(module);
      const node = LiteGraph.createNode(typeNameForModule(module));
      node.title = moduleDisplayName(module);
      node.pos = storedOrDefaultPosition(instance.instanceId, index, positionsStorageKey());
      node.color = palette.color;
      node.bgcolor = palette.bgcolor;
      node.boxcolor = palette.boxcolor;
      suppressEmit = true;
      graph.add(node);
      suppressEmit = false;
      trackGraphNode(node, instance);
      node.properties = { ...(node.properties || {}), ...(instance.config || {}) };
      syncNodeWidgetsFromProperties(node);
      if (module.moduleId === "graph-output" && node.properties.dataKey) {
        node.title = `Output: ${node.properties.dataKey}`;
      }
      return node;
    }

    const graphNodesByInstanceId = new Map();
    migrateLegacyStoredPositions(positionsStorageKey(meta || null), state.nodeIds || []);
    suppressEmit = true;
    state.nodeIds.forEach((instanceId, index) => {
      const instance = state.instances[instanceId];
      if (!instance) return;
      const node = createGraphNode(instance, index);
      if (node) graphNodesByInstanceId.set(instanceId, node);
    });

    state.nodeIds.forEach((instanceId) => {
      const instance = state.instances[instanceId];
      const node = graphNodesByInstanceId.get(instanceId);
      if (!instance || !node) return;
      Object.entries(instance.inputs || {}).forEach(([portName, wire]) => {
        if (!wire || wire === defaultNullWire()) return;
        const producerEntry = Object.values(state.instances).find((candidate) => Object.values(candidate.outputs || {}).includes(wire));
        if (!producerEntry) return;
        const sourceNode = graphNodesByInstanceId.get(producerEntry.instanceId);
        if (!sourceNode) return;
        const sourcePortName = Object.entries(producerEntry.outputs || {}).find(([, outputWire]) => outputWire === wire)?.[0];
        if (!sourcePortName) return;
        sourceNode.connect(sourcePortName, node, portName);
      });
    });
    suppressEmit = false;

    function addNode(moduleId, options = {}) {
      ensureHistoryBaseline();
      const module = moduleById.get(moduleId);
      if (!module) return;
      const {
        configOverride = undefined,
        preferredPos = null,
        focus = true,
        commitHistory = true,
        syncHistory = true,
      } = options;
      if (syncHistory) syncCurrentHistoryContext();
      const instanceId = uniqueInstanceId(module.moduleId);
      const instance = createDraftInstance(module, instanceId, configOverride);
      state.instances[instanceId] = instance;
      state.nodeIds.push(instanceId);
      const node = createGraphNode(instance, graph._nodes.length);
      if (node) {
        node.pos = findOpenNodePosition(node, preferredPos || nextViewportBasePosition(node), node);
        if (focus) focusNewNode(node);
        graph.setDirtyCanvas(true, true);
        if (commitHistory) commitGraphStructureChange();
      }
      return node;
    }

    function duplicateSelectedNodes() {
      ensureHistoryBaseline();
      const payload = serializeGraphNodes(selectedGraphNodes());
      if (!payload) return false;
      return pasteClipboardData(payload, {
        basePos: [payload.origin.x + 88, payload.origin.y + 64],
        bumpPasteCount: false,
      });
    }

    function arrangeNodes() {
      syncCurrentHistoryContext();
      const inDegree = new Map();
      const adjacency = new Map();
      graph._nodes.forEach((node) => {
        inDegree.set(node.id, 0);
        adjacency.set(node.id, []);
      });
      Object.values(graph.links).forEach((link) => {
        if (!inDegree.has(link.target_id)) return;
        inDegree.set(link.target_id, (inDegree.get(link.target_id) || 0) + 1);
        adjacency.get(link.origin_id)?.push(link.target_id);
      });
      const queue = [...graph._nodes.filter((node) => (inDegree.get(node.id) || 0) === 0)];
      const layers = [];
      const visited = new Set();
      while (queue.length) {
        const layer = [...queue];
        layers.push(layer);
        queue.length = 0;
        layer.forEach((node) => {
          visited.add(node.id);
          (adjacency.get(node.id) || []).forEach((targetId) => {
            inDegree.set(targetId, (inDegree.get(targetId) || 1) - 1);
            if ((inDegree.get(targetId) || 0) === 0) {
              const target = graph.getNodeById(targetId);
              if (target && !visited.has(target.id)) queue.push(target);
            }
          });
        });
      }
      if (!layers.length) layers.push([...graph._nodes]);
      layers.forEach((layer, layerIndex) => {
        layer.forEach((node, rowIndex) => {
          node.pos = [120 + layerIndex * 420, 80 + rowIndex * 220];
        });
      });
      graph.setDirtyCanvas(true, true);
      commitNodePositionChange();
    }

    function fitView() {
      const nodes = (graph._nodes || []).filter((node) => node?.graph === graph);
      if (!nodes.length || !frameNodes(nodes, { padding: 84, minScale: 0.35, maxScale: 1.12, persistViewport: false })) {
        canvas.ds.scale = 1;
        canvas.ds.offset[0] = 48;
        canvas.ds.offset[1] = 28;
        graph.setDirtyCanvas(true, true);
      }
      scheduleViewportPersist();
    }

    root.querySelector("[data-alpha-add-node]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      addNode(root.querySelector("[data-alpha-node-select]")?.value || "");
    });
    root.querySelector("[data-alpha-search]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      const bounds = canvasEl.getBoundingClientRect();
      const clientX = bounds.left + Math.min(bounds.width * 0.5, 420);
      const clientY = bounds.top + Math.min(bounds.height * 0.2, 180);
      LiteGraph.LGraphCanvas.active_canvas = canvas;
      canvas.showSearchBox({
        clientX,
        clientY,
        layerY: Math.min(bounds.height * 0.2, 180),
      }, {
        show_all_if_empty: true,
        do_type_filter: false,
      });
    });
    root.querySelector("[data-alpha-undo]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      undoHistory();
    });
    root.querySelector("[data-alpha-redo]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      redoHistory();
    });
    root.querySelector("[data-alpha-arrange]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      arrangeNodes();
    });
    root.querySelector("[data-alpha-fit]")?.addEventListener("click", () => {
      if (guardBusyAction()) return;
      fitView();
    });
    root.querySelectorAll("[data-alpha-meta]")?.forEach((input) => {
      input.addEventListener("input", () => {
        if (guardBusyAction()) return;
        emitMetaChange({ recordHistory: false });
      });
      input.addEventListener("change", () => {
        if (guardBusyAction()) return;
        emitMetaChange({ recordHistory: true });
      });
    });
    root.querySelector("[data-alpha-reload]")?.addEventListener("click", async () => {
      const actions = window.__tradePipelineActions;
      if (!actions?.loadPipelineFormFromAttachment) return;
      if (isReloadInFlight()) {
        setReloadEnabled();
        setStatus("Reload in progress", true);
        return;
      }
      if (isAttachInFlight()) {
        setReloadEnabled();
        setStatus("Attach in progress", true);
        return;
      }
      flushPendingEmit();
      try {
        reloadInFlight = true;
        syncSharedBusyState();
        setReloadEnabled();
        setAttachEnabled();
        setStatus("Reloading...");
        await actions.loadPipelineFormFromAttachment({ preferDraft: false, discardDraft: true });
        root.__setBlueprintStatus?.("Reloaded");
      } catch (error) {
        setStatus(error?.message || "Reload failed", true);
      } finally {
        reloadInFlight = false;
        syncSharedBusyState();
        setReloadEnabled();
        setAttachEnabled();
      }
    });
    root.querySelector("[data-alpha-attach]")?.addEventListener("click", async () => {
      const actions = window.__tradePipelineActions;
      if (!actions?.attachCurrentPipeline) return;
      if (isReloadInFlight()) {
        setAttachEnabled();
        setStatus("Reload in progress", true);
        return;
      }
      flushPendingEmit();
      if (validationState.errors.length) {
        setStatus(validationState.errors[0]?.message || "Fix graph errors before attaching", true);
        renderValidationState();
        return;
      }
      try {
        attachInFlight = true;
        syncSharedBusyState();
        setAttachEnabled();
        setReloadEnabled();
        setStatus("Attaching...");
        const response = await actions.attachCurrentPipeline({ redirect: false });
        const iterationId = response?.iteration?.iterationId || response?.liveManifestPath || "attached";
        root.__setBlueprintStatus?.(iterationId);
      } catch (error) {
        setStatus(error?.message || "Attach failed", true);
      } finally {
        attachInFlight = false;
        syncSharedBusyState();
        setAttachEnabled();
        setReloadEnabled();
      }
    });

    validationEl?.addEventListener("click", (event) => {
      if (guardBusyAction()) return;
      const target = event.target.closest("[data-alpha-node-id]");
      if (!target) return;
      const rawNodeId = target.dataset.alphaNodeId;
      const parsedNodeId = Number(rawNodeId);
      const node = graph.getNodeById(Number.isNaN(parsedNodeId) ? rawNodeId : parsedNodeId);
      if (!node) return;
      canvas.deselectAllNodes?.();
      canvas.selectNode?.(node);
      focusNewNode(node);
    });

    inspectorEl?.addEventListener("click", (event) => {
      if (guardBusyAction()) return;
      const explorerAction = event.target.closest("[data-alpha-explorer-action]");
      if (explorerAction) {
        const action = explorerAction.dataset.alphaExplorerAction;
        if (action === "select-filtered") {
          selectExplorerNodes(filteredExplorerNodes());
        } else if (action === "arrange-filtered") {
          arrangeNodeSubset(filteredExplorerNodes());
        } else if (action === "frame-filtered") {
          frameNodes(filteredExplorerNodes());
        }
        return;
      }
      const modeButton = event.target.closest("[data-alpha-explorer-mode]");
      if (modeButton) {
        const requestedMode = modeButton.dataset.alphaExplorerMode;
        inspectorExplorerMode = requestedMode === "issues" || requestedMode === "outputs" ? requestedMode : "all";
        inspectorExplorerActiveIndex = 0;
        renderInspector();
        window.requestAnimationFrame(() => focusExplorerFilter());
        return;
      }
      const actionButton = event.target.closest("[data-alpha-inspector-action]");
      if (actionButton) {
        const action = actionButton.dataset.alphaInspectorAction;
        if (action === "copy") copySelectedNodes();
        else if (action === "cut") cutSelectedNodes();
        else if (action === "duplicate") duplicateSelectedNodes();
        else if (action === "delete") deleteSelectedNodes();
        else if (action === "arrange-selection") arrangeNodeSubset(selectedGraphNodes());
        else if (action === "frame-selection") frameNodes(selectedGraphNodes());
        else if (action === "select-upstream") selectNodeChain(selectedPrimaryNode(), "upstream");
        else if (action === "select-downstream") selectNodeChain(selectedPrimaryNode(), "downstream");
        return;
      }
      const portActionButton = event.target.closest("[data-alpha-port-action]");
      if (portActionButton) {
        const node = selectedPrimaryNode();
        if (!node) return;
        const action = portActionButton.dataset.alphaPortAction;
        const side = portActionButton.dataset.alphaPortSide;
        const portIndex = Number.parseInt(portActionButton.dataset.alphaPortIndex || "", 10);
        if (action === "close-existing-picker") {
          setInspectorPortPicker(null);
          return;
        }
        if (Number.isNaN(portIndex)) return;
        if (action === "open-existing-picker" && (side === "input" || side === "output")) {
          setInspectorPortPicker({
            nodeId: node.id,
            side,
            slotIndex: portIndex,
            query: "",
          });
          return;
        }
        if (action === "disconnect" && side === "input") {
          disconnectInspectorInput(node, portIndex);
          return;
        }
        if (action === "disconnect-output-target" && side === "output") {
          const targetNodeId = Number.parseInt(portActionButton.dataset.alphaTargetNodeId || "", 10);
          if (Number.isNaN(targetNodeId)) return;
          disconnectInspectorOutputTarget(node, portIndex, targetNodeId);
          return;
        }
        if (action === "connect-existing-choice" && side === "input") {
          const sourceNodeId = Number.parseInt(portActionButton.dataset.alphaSourceNodeId || "", 10);
          const sourceSlotIndex = Number.parseInt(portActionButton.dataset.alphaSourceSlotIndex || "", 10);
          if (Number.isNaN(sourceNodeId) || Number.isNaN(sourceSlotIndex)) return;
          connectExistingToInput(node, portIndex, sourceNodeId, sourceSlotIndex);
          return;
        }
        if (action === "connect-existing-choice" && side === "output") {
          const targetNodeId = Number.parseInt(portActionButton.dataset.alphaTargetNodeId || "", 10);
          const targetSlotIndex = Number.parseInt(portActionButton.dataset.alphaTargetSlotIndex || "", 10);
          if (Number.isNaN(targetNodeId) || Number.isNaN(targetSlotIndex)) return;
          connectOutputToExisting(node, portIndex, targetNodeId, targetSlotIndex);
          return;
        }
        if ((action === "connect" || action === "replace") && (side === "input" || side === "output")) {
          openInspectorPortSearch(node, side, portIndex);
          return;
        }
      }
      const nodeButton = event.target.closest("[data-alpha-node-id]");
      if (!nodeButton) return;
      const rawNodeId = nodeButton.dataset.alphaNodeId;
      const parsedNodeId = Number(rawNodeId);
      const node = graph.getNodeById(Number.isNaN(parsedNodeId) ? rawNodeId : parsedNodeId);
      if (!node) return;
      focusNewNode(node);
    });

    inspectorEl?.addEventListener("input", (event) => {
      if (guardBusyAction()) return;
      const explorerInput = event.target.closest("[data-alpha-explorer-filter]");
      if (explorerInput) {
        inspectorExplorerQuery = explorerInput.value || "";
        inspectorExplorerActiveIndex = 0;
        renderInspector();
        return;
      }
      const filterInput = event.target.closest("[data-alpha-port-picker-filter]");
      if (!filterInput || !inspectorPortPicker) return;
      const side = filterInput.dataset.alphaPortPickerFilter;
      const slotIndex = Number.parseInt(filterInput.dataset.alphaPortIndex || "", 10);
      if (side !== inspectorPortPicker.side || Number.isNaN(slotIndex) || slotIndex !== inspectorPortPicker.slotIndex) return;
      inspectorPortPicker = {
        ...inspectorPortPicker,
        query: filterInput.value || "",
      };
      inspectorPortPickerActiveIndex = 0;
      renderInspector();
    });

    inspectorEl?.addEventListener("keydown", (event) => {
      if (isBlueprintBusy()) {
        event.preventDefault();
        setStatus(busyStatusMessage(), true);
        return;
      }
      const explorerInput = event.target.closest("[data-alpha-explorer-filter]");
      if (explorerInput) {
        const nodes = filteredExplorerNodes();
        if (event.key === "ArrowDown") {
          event.preventDefault();
          inspectorExplorerActiveIndex = normalizedListIndex(inspectorExplorerActiveIndex + 1, nodes.length);
          renderInspector();
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          inspectorExplorerActiveIndex = normalizedListIndex(inspectorExplorerActiveIndex - 1, nodes.length);
          renderInspector();
          return;
        }
        if (event.key === "Home") {
          event.preventDefault();
          inspectorExplorerActiveIndex = normalizedListIndex(0, nodes.length);
          renderInspector();
          return;
        }
        if (event.key === "End") {
          event.preventDefault();
          inspectorExplorerActiveIndex = normalizedListIndex(nodes.length - 1, nodes.length);
          renderInspector();
          return;
        }
        if (event.key === "Enter") {
          event.preventDefault();
          activateExplorerItemAt(inspectorExplorerActiveIndex);
        }
        if (event.key === "Escape" && explorerInput.value) {
          event.preventDefault();
          inspectorExplorerQuery = "";
          inspectorExplorerActiveIndex = 0;
          renderInspector();
        }
        return;
      }
      const filterInput = event.target.closest("[data-alpha-port-picker-filter]");
      if (!filterInput) return;
      const options = [...(inspectorEl?.querySelectorAll(".alpha-litegraph-port-picker-item") || [])];
      if (event.key === "ArrowDown") {
        event.preventDefault();
        inspectorPortPickerActiveIndex = normalizedListIndex(inspectorPortPickerActiveIndex + 1, options.length);
        renderInspector();
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        inspectorPortPickerActiveIndex = normalizedListIndex(inspectorPortPickerActiveIndex - 1, options.length);
        renderInspector();
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        inspectorPortPickerActiveIndex = normalizedListIndex(0, options.length);
        renderInspector();
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        inspectorPortPickerActiveIndex = normalizedListIndex(options.length - 1, options.length);
        renderInspector();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        activatePortPickerOptionAt(inspectorPortPickerActiveIndex);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setInspectorPortPicker(null);
      }
    });

    graph.onBeforeChange = () => {
      if (busyStateRestoring) return;
      if (suppressEmit) return;
      if (suppressAutoStructureSync > 0) return;
      syncCurrentHistoryContext();
    };
    graph.onAfterChange = () => {
      if (busyStateRestoring) return;
      if (suppressEmit) return;
      if (suppressAutoStructureSync > 0) return;
      queueEmit();
    };
    graph.onNodeConnectionChange = () => {
      if (busyStateRestoring) return;
      if (suppressEmit) return;
      if (suppressAutoStructureSync > 0) {
        renderInspector();
        return;
      }
      commitGraphStructureChange({ coalesceHistory: true });
      renderInspector();
    };
    graph.on_change = () => {
      if (busyStateRestoring) return;
      if (suppressEmit) return;
      if (suppressAutoStructureSync > 0) return;
      queueEmit();
    };
    graph.onNodeAdded = (node) => {
      if (busyStateRestoring) return;
      if (suppressEmit) return;
      if (nodeByGraphId.has(node.id)) return;
      const module = moduleByType.get(node.type);
      if (!module) return;
      const instanceId = uniqueInstanceId(module.moduleId);
      const instance = createDraftInstance(module, instanceId, node.properties || {});
      trackGraphNode(node, instance);
      if (suppressAutoStructureSync > 0) return;
      commitGraphStructureChange({ coalesceHistory: true });
    };
    graph.onNodeRemoved = (node) => {
      if (busyStateRestoring) return;
      const meta = nodeByGraphId.get(node.id);
      if (meta?.instanceId) deleteStoredPosition(meta.instanceId, positionsStorageKey());
      nodeByGraphId.delete(node.id);
      if (suppressEmit || suppressAutoStructureSync > 0) return;
      commitGraphStructureChange({ coalesceHistory: true });
    };
    canvas.onNodeMoved = () => {
      if (busyStateRestoring) return;
      commitNodePositionChange();
    };
    canvas.onSelectionChange = renderInspector;

    function isEditableTarget(target) {
      return Boolean(target && (
        target.tagName === "INPUT"
        || target.tagName === "TEXTAREA"
        || target.tagName === "SELECT"
        || target.isContentEditable
      ));
    }

    function isBlueprintVisible() {
      if (!root?.isConnected) return false;
      if (String(location.pathname || "").toLowerCase() === "/blueprint") return true;
      const pipelineViewActive = document.getElementById("pipeline")?.classList.contains("active");
      const activeSection = root.closest(".pipeline-section")?.classList.contains("active");
      return Boolean(pipelineViewActive && activeSection);
    }

    function onKeyDown(event) {
      if (!isBlueprintVisible()) return;
      const editableTarget = isEditableTarget(event.target);
      if (isBlueprintBusy()) {
        const key = event.key.toLowerCase();
        const shortcutPressed = (
          ((event.ctrlKey || event.metaKey) && ["s", "k", "d", "c", "x", "v", "a", "z", "y"].includes(key))
          || event.key === "Delete"
          || event.key === "Backspace"
        );
        if (shortcutPressed && !editableTarget) {
          event.preventDefault();
          setStatus(busyStatusMessage(), true);
        }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        root.querySelector("[data-alpha-attach]")?.click();
        return;
      }
      if (editableTarget) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        root.querySelector("[data-alpha-search]")?.click();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") {
        event.preventDefault();
        duplicateSelectedNodes();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c") {
        event.preventDefault();
        copySelectedNodes();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "x") {
        event.preventDefault();
        cutSelectedNodes();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") {
        event.preventDefault();
        pasteClipboard();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
        event.preventDefault();
        selectAllGraphNodes();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redoHistory();
        else undoHistory();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redoHistory();
        return;
      }
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const selected = selectedGraphNodes();
      if (!selected.length) return;
      event.preventDefault();
      deleteSelectedNodes();
    }
    window.addEventListener("keydown", onKeyDown);

    function onPageHide() {
      if (busyStateRestoring) return;
      if (!isBlueprintBusy()) {
        flushPendingEmit();
      }
      flushViewportPersist();
    }
    window.addEventListener("pagehide", onPageHide);

    const resizeObserver = new ResizeObserver(() => {
      updateFixedLayoutMetrics();
      resizeCanvas();
    });
    resizeObserver.observe(stageEl);
    if (toolbarEl) resizeObserver.observe(toolbarEl);
    if (validationEl) resizeObserver.observe(validationEl);

    updateFixedLayoutMetrics();
    resizeCanvas();
    const storedViewport = readStoredViewport();
    if (storedViewport) {
      canvas.ds.scale = storedViewport.scale;
      canvas.ds.offset[0] = storedViewport.offset[0];
      canvas.ds.offset[1] = storedViewport.offset[1];
      graph.setDirtyCanvas(true, true);
    } else {
      fitView();
    }
    ensureHistoryBaseline();
    refreshValidation();
    renderInspector();
    queueEmit();
    window.setTimeout(() => {
      markDirty = true;
    }, 0);

    function cleanup(options = {}) {
      const {
        flushPending = true,
      } = options;
      if (flushPending && !isBlueprintBusy() && !busyStateRestoring) {
        flushPendingEmit();
      }
      flushViewportPersist();
      if (emitTimer) clearTimeout(emitTimer);
      pendingHistoryEntry = null;
      releaseHistoryContextLock();
      resizeObserver.disconnect();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pagehide", onPageHide);
      clearOrphanSearchBoxes();
      if (typeof canvas.stopRendering === "function") canvas.stopRendering();
      if (typeof graph.stop === "function") graph.stop();
      LiteGraph.isValidConnection = previousValidConnection;
      root.__liteGraphGraph = null;
      root.__liteGraphCanvas = null;
      root.__liteGraphNodeMeta = null;
      root.__liteGraphLastSnapshot = null;
      root.__alphaBlueprintCleanup = null;
      root.__setBlueprintStatus = null;
      root.__syncAttachState = null;
      root.__flushPendingEmit = null;
      root.__refreshLayout = null;
      root.__reframeView = null;
      root.__undoHistory = null;
      root.__redoHistory = null;
      root.__copySelection = null;
      root.__cutSelection = null;
      root.__pasteClipboard = null;
      root.__selectAllGraphNodes = null;
      root.__selectAllNodes = null;
      root.__graphValidation = null;
      try {
        canvas.clear();
      } catch {}
    }

    root.__liteGraphGraph = graph;
    root.__liteGraphCanvas = canvas;
    root.__liteGraphNodeMeta = nodeByGraphId;
    root.__liteGraphLastSnapshot = null;
    root.__setBlueprintStatus = setStatus;
    root.__syncAttachState = () => {
      setAttachEnabled();
      setReloadEnabled();
    };
    root.__flushPendingEmit = flushPendingEmit;
    root.__refreshLayout = resizeCanvas;
    root.__reframeView = fitView;
    root.__undoHistory = undoHistory;
    root.__redoHistory = redoHistory;
      root.__copySelection = copySelectedNodes;
      root.__cutSelection = cutSelectedNodes;
      root.__pasteClipboard = pasteClipboard;
      root.__selectAllGraphNodes = selectAllGraphNodes;
      root.__selectAllNodes = selectAllGraphNodes;
      root.__graphValidation = () => cloneJson(validationState);
      root.__alphaBlueprintCleanup = cleanup;
    return { cleanup, refreshLayout: resizeCanvas, reframeView: fitView };
  }

  window.AlphaBlueprintLiteGraph = { mount };
}());
