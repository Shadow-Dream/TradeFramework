(function () {
  "use strict";

  const LiteGraph = window.LiteGraph;
  const forms = window.TradeModuleForms;
  const FILTER = "trade-module-graph";
  const INPUT_TYPE = "trade-graph/boundary-input";
  const OUTPUT_TYPE = "trade-graph/boundary-output";
  const positionPrefix = "trade.module-graph.positions.v1:";
  const viewportPrefix = "trade.module-graph.viewport.v1:";

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
    return `${module.name || humanize(module.moduleId)} @ ${module.version}`;
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
      if (usedLabels.has(label)) label = `${label} [${source || "default"}]`;
      while (usedLabels.has(label)) label += "*";
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
          this.size = [300, 126];
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
            this.title = this.properties.dataKey ? `Input: ${this.properties.dataKey}` : "Graph Input";
            this.graph?._tradeSchedule?.();
          });
          this.addWidget("text", "Wire", "", (value) => {
            this.properties.wire = String(value || "").trim();
            this.graph?._tradeSchedule?.();
          });
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
          this.size = [300, 108];
          this.color = "#ffe8d4";
          this.bgcolor = "#fff6ed";
          this.boxcolor = "#c2410c";
          this.properties = { dataKey: "" };
          this.addInput("data", 0);
          this.addWidget("text", "Data key", "", (value) => {
            this.properties.dataKey = String(value || "").trim();
            this.title = this.properties.dataKey ? `Output: ${this.properties.dataKey}` : "Graph Output";
            this.graph?._tradeSchedule?.();
          });
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
          this.title = module.name || humanize(module.moduleId);
          this.size = [360, 150];
          this.color = "#e0ecff";
          this.bgcolor = "#f2f7ff";
          this.boxcolor = "#1d4ed8";
          this.properties = {};
          Object.keys(module.ports?.inputs || {}).forEach((name) => {
            this.addInput(name, 0);
          });
          Object.keys(module.ports?.outputs || {}).forEach((name) => {
            this.addOutput(name, 0);
          });
          const computed = this.computeSize();
          this.size = [Math.max(360, computed[0]), Math.max(130, computed[1] + 10)];
        }

        onConnectionsChange() { this.graph?._tradeSchedule?.(); }
      }
      ModuleNode.title = module.name || humanize(module.moduleId);
      ModuleNode.desc = module.description || moduleKey(module);
      ModuleNode.filter = FILTER;
      LiteGraph.registerNodeType(type, ModuleNode);
    });
  }

  function mount(options = {}) {
    const {
      root,
      modules = [],
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

    const availableModules = modules
      .filter((module) => module.kind === moduleKind && module.status === "archived")
      .sort((left, right) => moduleLabel(left).localeCompare(moduleLabel(right)));
    const definitionByKey = new Map(availableModules.map((module) => [moduleKey(module), module]));
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
    let currentInstances = clone(instances || {});
    let currentDocument = graphDocument(alphaGraph);
    const nodeMeta = new Map();
    let selectedInspectorNodeId = null;
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

    function contextName() {
      const editedName = resourceEditor?.contextName?.(clone(resourceValues));
      return String(editedName || meta.name || contextId);
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
    registerModuleTypes(availableModules);

    const versionRows = Array.isArray(versions) ? [...versions].reverse() : [];
    root.innerHTML = `
      <div class="alpha-litegraph-shell">
        <div class="alpha-litegraph-toolbar">
          <div class="alpha-litegraph-toolbar-row alpha-litegraph-toolbar-row-primary">
            <div class="alpha-litegraph-context"><span>${escapeHtml(contextLabel)}</span><strong data-graph-context-name>${escapeHtml(contextName())}</strong></div>
            <div class="alpha-litegraph-toolbar-actions">
              <div class="alpha-litegraph-action-group alpha-litegraph-graph-actions" aria-label="Graph boundaries">
                <button type="button" data-graph-input>Add Input</button>
                <button type="button" data-graph-output>Add Output</button>
              </div>
              <div class="alpha-litegraph-action-group alpha-litegraph-view-tools" aria-label="Canvas view">
                <button type="button" data-graph-arrange>Arrange</button>
                <button type="button" data-graph-fit>Fit</button>
              </div>
              <button class="alpha-litegraph-back-action" type="button" data-graph-back>${escapeHtml(backLabel)}</button>
            </div>
          </div>
          <div class="alpha-litegraph-toolbar-row alpha-litegraph-toolbar-row-secondary">
            <div class="alpha-litegraph-toolbar-group alpha-litegraph-module-actions">
              <label class="alpha-litegraph-picker"><span>Module</span><select data-graph-module>
                <option value="">Select an archived ${escapeHtml(moduleKind)} Module</option>
                ${availableModules.map((module) => `<option value="${escapeHtml(moduleKey(module))}">${escapeHtml(moduleLabel(module))}</option>`).join("")}
              </select></label>
              <button type="button" data-graph-add-module>Add Module</button>
            </div>
            <div class="alpha-litegraph-toolbar-group alpha-litegraph-version-actions">
              <label class="alpha-litegraph-picker" data-graph-version-wrap><span>Version</span><select data-graph-version>
                ${versionRows.map((row) => `<option value="${escapeHtml(row.version)}" ${String(row.version) === String(loadedVersion) ? "selected" : ""}>v${escapeHtml(row.version)}</option>`).join("")}
              </select></label>
              <button type="button" data-graph-load>Load Version</button>
              <button class="alpha-litegraph-save-action" type="button" data-graph-save>Save Version</button>
            </div>
          </div>
        </div>
        <div class="alpha-litegraph-body">
          <div class="alpha-litegraph-stage"><canvas class="alpha-litegraph-canvas"></canvas></div>
          <aside class="alpha-litegraph-inspector">
            <div class="alpha-litegraph-explorer-head"><div><strong>${escapeHtml(graphLabel)}</strong><span>Graph boundaries are not Modules</span></div></div>
            <div class="alpha-litegraph-inspector-content" data-graph-inspector></div>
          </aside>
        </div>
        <div class="alpha-litegraph-footer">
          <div class="alpha-litegraph-validation" data-graph-validation></div>
          <span class="alpha-litegraph-status" data-graph-status data-state="saved">Saved</span>
        </div>
      </div>`;

    const canvasElement = root.querySelector("canvas");
    const stageElement = root.querySelector(".alpha-litegraph-stage");
    const statusElement = root.querySelector("[data-graph-status]");
    const validationElement = root.querySelector("[data-graph-validation]");
    const inspectorElement = root.querySelector("[data-graph-inspector]");
    const moduleSelect = root.querySelector("[data-graph-module]");
    const saveAction = actions.onSave || window.__tradePipelineActions?.saveCurrentPipelineVersion;
    const loadAction = actions.onLoad || window.__tradePipelineActions?.loadPipelineVersion;
    root.querySelector("[data-graph-save]").hidden = !saveAction;
    root.querySelector("[data-graph-load]").hidden = !loadAction;
    root.querySelector("[data-graph-version-wrap]").hidden = !loadAction;

    const canvas = new LiteGraph.LGraphCanvas(canvasElement, graph, {
      autoresize: false,
      background_image: null,
    });
    canvas.filter = FILTER;
    canvas.allow_reconnect_links = true;
    canvas.connections_width = 4;
    canvas.render_connections_border = true;

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
      setNodeWidget(node, 0, node.properties.dataKey);
      if (input) {
        node.properties.wire = String(binding?.wire || `input.${id}`);
        setNodeWidget(node, 1, node.properties.wire);
        if (namedInputSourceNames.length) {
          node.properties.source = String(binding?.source || "");
          node.addWidget("combo", "Source", inputSourceLabels[node.properties.source], (value) => {
            node.properties.source = inputSourceByLabel[String(value)] ?? "";
            node.graph?._tradeSchedule?.();
          }, { values: inputSourceWidgetValues });
          node.size = [300, 150];
        }
      }
      node.title = node.properties.dataKey
        ? `${input ? "Input" : "Output"}: ${node.properties.dataKey}`
        : `Graph ${input ? "Input" : "Output"}`;
      graph.add(node);
      nodeMeta.set(node.id, { entity: kind, id });
      return node;
    }

    function addModule(instance, definition, index) {
      const node = LiteGraph.createNode(moduleType(definition));
      if (!node) throw new Error(`Unable to create Module node ${moduleKey(definition)}.`);
      node.pos = positionFor(instance.instanceId, index);
      node.properties = clone(instance.config || {});
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
        const definition = definitionForInstance(instance);
        if (!instance || !definition) return;
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
          if (wire && seenWires.has(wire)) issues.push({ level: "error", nodeId: node.id, message: `Wire '${wire}' has multiple producers.` });
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
            issues.push({ level: "error", nodeId: node.id, message: `${item.id}.${port} is required.` });
          }
        });
        Object.keys(item.definition.ports?.outputs || {}).forEach((port, slot) => {
          const wire = outputWire(node, slot);
          if (seenWires.has(wire)) issues.push({ level: "error", nodeId: node.id, message: `Wire '${wire}' has multiple producers.` });
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
      root.__validationState = combined;
      if (result.errors.length) {
        validationElement.innerHTML = result.errors.slice(0, 4).map((issue) => `<button type="button" data-node-id="${issue.nodeId}" class="alpha-litegraph-validation-pill error">${escapeHtml(issue.message)}</button>`).join("");
      } else if (authorityValidation.state === "invalid") {
        validationElement.innerHTML = `<span class="alpha-litegraph-validation-pill error">${escapeHtml(authorityValidation.message)}</span>`;
      } else {
        validationElement.innerHTML = `<span class="muted">${escapeHtml(authorityValidation.message)}</span>`;
      }
      const saveButton = root.querySelector("[data-graph-save]");
      if (saveButton) {
        saveButton.disabled = Boolean(result.errors.length)
          || authorityValidation.state !== "valid";
      }
      return combined;
    }

    function renderInspector() {
      const rows = graph._nodes.map((node) => {
        const item = nodeMeta.get(node.id);
        if (!item) return "";
        const kind = item.entity === "module" ? item.definition.kind : `Graph ${humanize(item.entity)}`;
        const sourceLabel = item.entity === "input" && namedInputSourceNames.length
          ? inputSourceLabels[node.properties.source || ""] : "";
        const label = item.entity === "module" ? item.id : (node.properties.dataKey || item.id);
        return `<button class="alpha-litegraph-inspector-list-item" type="button" data-node-id="${node.id}"><span>${escapeHtml(label)}</span><small>${escapeHtml(sourceLabel || kind)}</small></button>`;
      }).join("");
      const selectedNode = graph.getNodeById(selectedInspectorNodeId);
      const selectedItem = selectedNode ? nodeMeta.get(selectedNode.id) : null;
      const resourceSection = resourceFields.length ? `
        <section class="alpha-litegraph-resource-editor">
          <div class="alpha-litegraph-resource-editor-head">
            <strong>${escapeHtml(resourceEditor.title || "Resource Details")}</strong>
            ${resourceEditor.description ? `<span>${escapeHtml(resourceEditor.description)}</span>` : ""}
          </div>
          <div class="alpha-litegraph-resource-fields">
            ${resourceFields.map((field) => `
              <label>
                <span>${escapeHtml(field.label)}</span>
                <input data-graph-resource-field="${escapeHtml(field.key)}"
                  value="${escapeHtml(resourceValues[field.key])}"
                  placeholder="${escapeHtml(field.placeholder || "")}"
                  ${field.required ? "required" : ""}
                  ${field.readOnly ? "readonly" : ""}
                  autocomplete="off" />
              </label>`).join("")}
          </div>
        </section>` : "";
      inspectorElement.innerHTML = `
        ${resourceSection}
        <div class="alpha-litegraph-inspector-list">${rows || '<div class="alpha-litegraph-inspector-empty muted">Add boundaries and Modules to build this Graph.</div>'}</div>
        <div data-graph-config-editor></div>`;
      inspectorElement.querySelectorAll("[data-graph-resource-field]").forEach((input) => {
        input.addEventListener("input", () => {
          resourceValues[input.dataset.graphResourceField] = input.value;
          resourceEditor.onChange?.(clone(resourceValues));
          const contextNode = root.querySelector("[data-graph-context-name]");
          if (contextNode) contextNode.textContent = contextName();
          scheduleEmit();
        });
      });
      if (selectedItem?.entity !== "module") return;
      const editor = inspectorElement.querySelector("[data-graph-config-editor]");
      editor.innerHTML = `<h4>${escapeHtml(selectedItem.id)} configuration</h4><div data-graph-config-fields></div><div class="dialog-error" data-graph-config-error hidden></div>`;
      const fields = editor.querySelector("[data-graph-config-fields]");
      const error = editor.querySelector("[data-graph-config-error]");
      forms.renderSchemaFields(
        fields,
        selectedItem.definition.configSchema,
        clone(selectedNode.properties || {}),
      );
      fields.addEventListener("change", () => {
        try {
          selectedNode.properties = forms.readSchemaFields(
            fields,
            selectedItem.definition.configSchema,
          );
          error.textContent = "";
          error.hidden = true;
          scheduleEmit();
        } catch (exception) {
          error.textContent = exception?.message || "Invalid Module configuration";
          error.hidden = false;
        }
      });
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
          return authorityValidation;
        });
      return authorityPromise;
    }

    function syncStatus(message = "") {
      statusElement.textContent = message || (dirty ? "Unsaved" : "Saved");
      statusElement.dataset.state = dirty ? "dirty" : "saved";
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
      const definition = definitionByKey.get(moduleSelect.value);
      if (!definition) return;
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
      node.pos = canvas.convertCanvasToOffset([canvasElement.width / 2, canvasElement.height / 2]);
      selectedInspectorNodeId = node.id;
      renderInspector();
      scheduleEmit();
      canvas.selectNode?.(node);
    }

    function arrange() {
      const groups = { input: [], module: [], output: [] };
      graph._nodes.forEach((node) => groups[nodeMeta.get(node.id)?.entity]?.push(node));
      ["input", "module", "output"].forEach((kind, column) => {
        groups[kind].forEach((node, row) => { node.pos = [80 + column * 460, 80 + row * 210]; });
      });
      graph.setDirtyCanvas(true, true);
      scheduleEmit();
    }

    function resize() {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(640, stageElement.clientWidth);
      const height = Math.max(480, stageElement.clientHeight);
      canvasElement.width = Math.round(width * ratio);
      canvasElement.height = Math.round(height * ratio);
      canvasElement.style.width = `${width}px`;
      canvasElement.style.height = `${height}px`;
      canvas.resize?.();
      graph.setDirtyCanvas(true, true);
    }

    root.querySelector("[data-graph-input]").addEventListener("click", () => addBoundaryFromUi("input"));
    root.querySelector("[data-graph-output]").addEventListener("click", () => addBoundaryFromUi("output"));
    root.querySelector("[data-graph-add-module]").addEventListener("click", addModuleFromUi);
    root.querySelector("[data-graph-arrange]").addEventListener("click", arrange);
    root.querySelector("[data-graph-fit]").addEventListener("click", () => canvas.fit?.(true));
    root.querySelector("[data-graph-back]").addEventListener("click", () => (actions.onBack || window.__tradePipelineActions?.backToPipeline)?.());
    root.querySelector("[data-graph-save]").addEventListener("click", async () => {
      emit();
      await authorityPromise;
      const result = validateAndRender();
      if (result.errors.length) return;
      syncStatus("Saving…");
      try {
        await saveAction?.();
        dirty = false;
        syncStatus();
      } catch (error) {
        dirty = true;
        syncStatus(error?.message || "Save failed");
      }
    });
    root.querySelector("[data-graph-load]").addEventListener("click", async () => {
      const version = root.querySelector("[data-graph-version]").value;
      if (version) await loadAction?.(version);
    });
    validationElement.addEventListener("click", (event) => {
      const node = graph.getNodeById(Number(event.target.closest("[data-node-id]")?.dataset.nodeId));
      if (node) canvas.centerOnNode?.(node);
    });
    inspectorElement.addEventListener("click", (event) => {
      const node = graph.getNodeById(Number(event.target.closest("[data-node-id]")?.dataset.nodeId));
      if (!node) return;
      selectedInspectorNodeId = node.id;
      canvas.deselectAllNodes?.();
      canvas.selectNode?.(node);
      canvas.centerOnNode?.(node);
      renderInspector();
    });
    graph.onNodeRemoved = () => {
      if (!graph.getNodeById(selectedInspectorNodeId)) selectedInspectorNodeId = null;
      renderInspector();
      scheduleEmit();
    };
    graph.onConnectionChange = scheduleEmit;

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(stageElement);
    const onKeyDown = (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
      if (!root.isConnected) return;
      event.preventDefault();
      root.querySelector("[data-graph-save]")?.click();
    };
    document.addEventListener("keydown", onKeyDown);

    root.__liteGraphGraph = graph;
    root.__liteGraphCanvas = canvas;
    root.__liteGraphNodeMeta = nodeMeta;
    root.__graphValidation = () => clone(validateAndRender());
    root.__flushPendingEmit = emit;
    root.__refreshLayout = resize;
    root.__syncSaveState = syncStatus;
    root.__setBlueprintStatus = (message, failed = false) => {
      statusElement.textContent = message || "";
      statusElement.dataset.state = failed ? "error" : "saved";
    };
    root.__syncFromAlphaGraphSnapshot = (next) => rebuild(next);
    root.__moduleGraphCleanup = ({ flushPending = false } = {}) => {
      if (destroyed) return;
      if (flushPending) emit();
      destroyed = true;
      clearTimeout(emitTimer);
      resizeObserver.disconnect();
      document.removeEventListener("keydown", onKeyDown);
      graph.stop?.();
      canvas.close?.();
      root.__liteGraphGraph = null;
      root.__liteGraphCanvas = null;
      root.__liteGraphNodeMeta = null;
      root.__moduleGraphCleanup = null;
    };

    rebuild();
    resize();
    try {
      const viewport = JSON.parse(localStorage.getItem(viewportKey) || "null");
      if (viewport?.scale) canvas.ds.scale = viewport.scale;
      if (Array.isArray(viewport?.offset)) canvas.ds.offset = [...viewport.offset];
    } catch {}
    canvas.onRenderBackground = () => {
      localStorage.setItem(viewportKey, JSON.stringify({ scale: canvas.ds.scale, offset: [...canvas.ds.offset] }));
    };
    return { graph, canvas, emit };
  }

  window.ModuleGraphLiteGraph = { mount };
}());
