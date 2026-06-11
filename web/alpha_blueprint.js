(function () {
  const forms = window.TradeModuleForms;
  const STAGE_PADDING = 48;
  const NODE_WIDTH = 168;
  const NODE_MIN_HEIGHT = 58;
  const PORT_ROW_HEIGHT = 18;
  const DEFAULT_CHAIN_LEFT_RATIO = 0.16;
  const DEFAULT_CHAIN_TOP_RATIO = 0.34;
  const DEFAULT_CHAIN_PAIR_GAP = 140;
  const DEFAULT_X_GAP = 180;
  const DEFAULT_Y_GAP = 140;

  function escapeHtml(value) {
    return forms.escapeHtml(value);
  }

  function defaultNullWire() {
    return "null";
  }

  function orderedPortNames(ports, direction) {
    return Object.keys(ports?.[direction] || {});
  }

  function moduleDisplayName(module) {
    const compactNames = {
      "price-source": "Price",
      "sma-indicator": "SMA",
    };
    return compactNames[module?.moduleId || ""] || forms.humanizeName(module?.moduleId || "node");
  }

  function compactPortLabel(value) {
    const map = {
      price: "P",
      open: "O",
      high: "H",
      low: "L",
      close: "C",
      volume: "V",
      value: "IN",
      sma: "SMA",
    };
    const text = String(value || "").toLowerCase();
    return map[text] || forms.humanizeName(text).slice(0, 4).toUpperCase();
  }

  function defaultOutputKey(moduleId, portName) {
    return `${moduleId}.${portName}`;
  }

  function uniqueWireName(state, preferred, currentInstanceId = "", currentPortName = "") {
    const used = new Set();
    Object.values(state.instances).forEach((instance) => {
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

  function primaryBlueprintModules(modules) {
    const preferred = ["price-source", "sma-indicator"];
    const byId = new Map(modules.map((module) => [module.moduleId, module]));
    const picked = preferred.map((id) => byId.get(id)).filter(Boolean);
    return picked.length ? picked : modules;
  }

  function buildProducerMap(instances) {
    const map = new Map();
    Object.values(instances).forEach((instance) => {
      Object.entries(instance.outputs || {}).forEach(([portName, wire]) => {
        if (wire) map.set(wire, { instanceId: instance.instanceId, portName });
      });
    });
    return map;
  }

  function graphHasCycle(instances) {
    const producerByWire = buildProducerMap(instances);
    const deps = new Map(Object.keys(instances).map((id) => [id, new Set()]));
    Object.values(instances).forEach((instance) => {
      Object.values(instance.inputs || {}).forEach((wire) => {
        if (!wire || wire === defaultNullWire()) return;
        const producer = producerByWire.get(wire);
        if (!producer) return;
        deps.get(instance.instanceId)?.add(producer.instanceId);
      });
    });
    const visiting = new Set();
    const visited = new Set();
    function visit(nodeId) {
      if (visited.has(nodeId)) return false;
      if (visiting.has(nodeId)) return true;
      visiting.add(nodeId);
      for (const dep of deps.get(nodeId) || []) {
        if (visit(dep)) return true;
      }
      visiting.delete(nodeId);
      visited.add(nodeId);
      return false;
    }
    return [...deps.keys()].some((nodeId) => visit(nodeId));
  }

  function cloneInitialState(modules, instances, alphaGraph) {
    const moduleById = new Map(modules.map((module) => [module.moduleId, module]));
    const requestedNodes = Array.isArray(alphaGraph?.nodes) ? alphaGraph.nodes.filter(Boolean) : null;
    const requested = requestedNodes?.length ? new Set(requestedNodes) : null;
    const map = {};
    Object.values(instances || {}).forEach((instance) => {
      if (instance.kind !== "Signal") return;
      if (requested && !requested.has(instance.instanceId)) return;
      const module = moduleById.get(instance.moduleId);
      if (!module) return;
      map[instance.instanceId] = {
        instanceId: instance.instanceId,
        moduleId: instance.moduleId,
        version: instance.version,
        kind: instance.kind,
        config: { ...(instance.config || {}) },
        inputs: { ...(instance.inputs || {}) },
        outputs: { ...(instance.outputs || {}) },
        ports: module.ports || { inputs: {}, outputs: {} },
      };
    });
    return {
      instances: map,
      nodeIds: requested ? requestedNodes.filter((id) => map[id]) : Object.keys(map),
      positions: {},
    };
  }

  function buildDemoInstance(state, module) {
    const instanceId = `${module.moduleId}.${Date.now().toString(36)}.${Math.random().toString(36).slice(2, 5)}`;
    return {
      instanceId,
      moduleId: module.moduleId,
      version: module.version,
      kind: module.kind,
      config: forms.schemaDefaults(module.configSchema || {}),
      inputs: Object.fromEntries(orderedPortNames(module.ports, "inputs").map((name) => [name, defaultNullWire()])),
      outputs: Object.fromEntries(
        orderedPortNames(module.ports, "outputs").map((name) => [name, uniqueWireName(state, defaultOutputKey(module.moduleId, name))]),
      ),
      ports: module.ports || { inputs: {}, outputs: {} },
    };
  }

  function serializeAlphaGraph(state) {
    return { nodes: [...state.nodeIds], outputs: {} };
  }

  function useDenseExpandedLayout(inputNames = [], outputNames = []) {
    return inputNames.length > 1 || outputNames.length > 2;
  }

  function denseRowCount(inputNames = [], outputNames = []) {
    const inputsOnly = inputNames.length > 0 && outputNames.length === 0;
    const outputsOnly = outputNames.length > 0 && inputNames.length === 0;
    if (inputsOnly) return Math.max(1, 1 + Math.ceil(Math.max(0, inputNames.length - 1) / 3));
    if (outputsOnly) return Math.max(1, 1 + Math.ceil(Math.max(0, outputNames.length - 1) / 3));
    return Math.max(1, Math.ceil(inputNames.length / 2), Math.ceil(outputNames.length / 2));
  }

  function nodeMetrics(instance, inputNames = null, outputNames = null, options = {}) {
    const resolvedInputs = inputNames || orderedPortNames(instance?.ports, "inputs");
    const resolvedOutputs = outputNames || orderedPortNames(instance?.ports, "outputs");
    const pendingSourceSingleOutput = !!options.pendingSourceSingleOutput;
    const pendingTargetSingleInput = !!options.pendingTargetSingleInput;
    const dense = useDenseExpandedLayout(resolvedInputs, resolvedOutputs);
    const rowCount = dense
      ? denseRowCount(resolvedInputs, resolvedOutputs)
      : Math.max(1, resolvedInputs.length, resolvedOutputs.length);
    const rowHeight = dense ? 16 : 20;
    const rowGap = dense ? 2 : 6;
    const bodyRowsHeight = rowCount * rowHeight + Math.max(0, rowCount - 1) * rowGap;
    const singleSideDense = dense && (!resolvedInputs.length || !resolvedOutputs.length);
    return {
      width: dense ? (singleSideDense ? NODE_WIDTH : 212) : NODE_WIDTH,
      height: Math.max(
        NODE_MIN_HEIGHT,
        ((pendingSourceSingleOutput || pendingTargetSingleInput) ? 60 : (dense ? 42 : 52)) + bodyRowsHeight,
      ),
    };
  }

  function mount(options) {
    const { root, modules, instances, alphaGraph, onChange } = options;
    if (!root) return null;
    root.__alphaBlueprintCleanup?.();

    root.innerHTML = `
      <div class="alpha-blueprint-shell">
        <div class="alpha-blueprint-body">
          <div class="alpha-blueprint-stage">
            <div class="alpha-blueprint-viewport">
              <svg class="alpha-graph-svg"></svg>
              <div class="alpha-graph-surface"></div>
            </div>
          </div>
        </div>
        <div class="alpha-blueprint-menu" hidden></div>
      </div>
    `;

    const shell = root.querySelector(".alpha-blueprint-shell");
    const stage = root.querySelector(".alpha-blueprint-stage");
    const viewport = root.querySelector(".alpha-blueprint-viewport");
    const surface = root.querySelector(".alpha-graph-surface");
    const svg = root.querySelector(".alpha-graph-svg");
    const menu = root.querySelector(".alpha-blueprint-menu");
    const moduleById = new Map(modules.map((module) => [module.moduleId, module]));
    const menuModules = primaryBlueprintModules(modules);
    const state = cloneInitialState(modules, instances, alphaGraph);

    if (!state.nodeIds.length) {
      const priceModule = moduleById.get("price-source");
      const smaModule = moduleById.get("sma-indicator");
      if (priceModule && smaModule) {
        const price = buildDemoInstance(state, priceModule);
        const sma = buildDemoInstance(state, smaModule);
        sma.inputs.value = price.outputs.price || defaultNullWire();
        state.instances[price.instanceId] = price;
        state.instances[sma.instanceId] = sma;
        state.nodeIds = [price.instanceId, sma.instanceId];
      }
    }

    let selectedNodeId = "";
    let pendingConnection = null;
    let draggingNode = null;
    let rafToken = 0;
    let autoLayout = true;
    let lastConnectedPair = null;
    let lastConnectedUntil = 0;
    let lastConnectedTimer = 0;

    shell.__debugState = state;

    function stageBounds() {
      const width = Math.max(stage.clientWidth || viewport.clientWidth || 960, 960);
      const height = Math.max(stage.clientHeight || viewport.clientHeight || 760, 760);
      return { width, height };
    }

    function layoutViewport() {
      const bounds = stageBounds();
      viewport.style.width = `${bounds.width}px`;
      viewport.style.height = `${bounds.height}px`;
      surface.style.width = `${bounds.width}px`;
      surface.style.height = `${bounds.height}px`;
      svg.setAttribute("width", String(bounds.width));
      svg.setAttribute("height", String(bounds.height));
      svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
    }

    function defaultPositionForIndex(index, metric) {
      const bounds = stageBounds();
      const activeIds = state.nodeIds.filter((id) => state.instances[id]);
      if (activeIds.length <= 2) {
        const metrics = activeIds.map((id) => {
          const ports = visiblePortNames(state.instances[id]);
          return nodeMetrics(state.instances[id], ports.inputs, ports.outputs);
        });
        const totalWidth = metrics.reduce((sum, item) => sum + item.width, 0) + Math.max(0, metrics.length - 1) * DEFAULT_CHAIN_PAIR_GAP;
        const startX = Math.max(STAGE_PADDING, Math.round((bounds.width - totalWidth) / 2));
        const maxHeight = metrics.reduce((sum, item) => Math.max(sum, item.height), 0);
        const centerY = Math.max(STAGE_PADDING, Math.round((bounds.height - maxHeight) / 2));
        const offsetX = metrics.slice(0, index).reduce((sum, item) => sum + item.width + DEFAULT_CHAIN_PAIR_GAP, 0);
        return {
          x: Math.max(STAGE_PADDING, Math.min(startX + offsetX, bounds.width - metric.width - STAGE_PADDING)),
          y: Math.max(STAGE_PADDING, Math.min(centerY, bounds.height - metric.height - STAGE_PADDING)),
        };
      }
      const row = Math.floor(index / 3);
      const column = index % 3;
      const startX = Math.max(STAGE_PADDING, Math.round(bounds.width * DEFAULT_CHAIN_LEFT_RATIO));
      const startY = Math.max(STAGE_PADDING, Math.round(bounds.height * DEFAULT_CHAIN_TOP_RATIO));
      const x = startX + column * (metric.width + DEFAULT_X_GAP);
      const y = startY + row * DEFAULT_Y_GAP;
      return {
        x: Math.max(STAGE_PADDING, Math.min(x, bounds.width - metric.width - STAGE_PADDING)),
        y: Math.max(STAGE_PADDING, Math.min(y, bounds.height - metric.height - STAGE_PADDING)),
      };
    }

    function relayoutDefaultNodes() {
      state.nodeIds.filter((id) => state.instances[id]).forEach((instanceId, index) => {
        const visiblePorts = visiblePortNames(state.instances[instanceId]);
        const metric = nodeMetrics(state.instances[instanceId], visiblePorts.inputs, visiblePorts.outputs);
        state.positions[instanceId] = defaultPositionForIndex(index, metric);
      });
    }

    function resolvedNodePosition(instanceId, metric, preferred = null) {
      const bounds = stageBounds();
      const index = Math.max(0, state.nodeIds.indexOf(instanceId));
      const base = preferred || state.positions[instanceId] || defaultPositionForIndex(index, metric);
      const next = {
        x: Math.max(STAGE_PADDING, Math.min(base.x ?? STAGE_PADDING, bounds.width - metric.width - STAGE_PADDING)),
        y: Math.max(STAGE_PADDING, Math.min(base.y ?? STAGE_PADDING, bounds.height - metric.height - STAGE_PADDING)),
      };
      state.positions[instanceId] = next;
      return next;
    }

    function clampAllNodePositions() {
      state.nodeIds.filter((id) => state.instances[id]).forEach((instanceId) => {
        const ports = visiblePortNames(state.instances[instanceId]);
        const metric = nodeMetrics(state.instances[instanceId], ports.inputs, ports.outputs);
        resolvedNodePosition(instanceId, metric, state.positions[instanceId]);
      });
    }

    function shouldAutoLayoutNow() {
      return autoLayout && !selectedNodeId && !pendingConnection && state.nodeIds.length <= 2;
    }

    function emitChange() {
      onChange?.({
        instances: Object.fromEntries(
          Object.entries(state.instances).map(([id, instance]) => [id, {
            instanceId: id,
            kind: instance.kind,
            moduleId: instance.moduleId,
            version: instance.version,
            config: instance.config,
            inputs: instance.inputs,
            outputs: instance.outputs,
          }]),
        ),
        alphaGraph: serializeAlphaGraph(state),
      });
    }

    function consumerPortNames(instance) {
      const names = [];
      Object.entries(instance.outputs || {}).forEach(([portName, wire]) => {
        if (!wire || wire === defaultNullWire()) return;
        const used = Object.values(state.instances).some((other) => (
          other.instanceId !== instance.instanceId
          && Object.values(other.inputs || {}).some((inputWire) => inputWire === wire)
        ));
        if (used) names.push(portName);
      });
      return names;
    }

    function outputPortIsConnected(instance, portName) {
      const wire = instance.outputs?.[portName];
      if (!wire || wire === defaultNullWire()) return false;
      return Object.values(state.instances).some((other) => (
        other.instanceId !== instance.instanceId
        && Object.values(other.inputs || {}).some((inputWire) => inputWire === wire)
      ));
    }

    function instanceHasConnectedInput(instance) {
      return Object.values(instance.inputs || {}).some((wire) => wire && wire !== defaultNullWire());
    }

    function instanceHasConnectedOutput(instance) {
      return Object.keys(instance.outputs || {}).some((portName) => outputPortIsConnected(instance, portName));
    }

    function nodeIsRelatedToSelected(instance) {
      if (!selectedNodeId || selectedNodeId === instance.instanceId) return false;
      const selected = state.instances[selectedNodeId];
      if (!selected) return false;
      const selectedOutputWires = new Set(Object.values(selected.outputs || {}).filter((wire) => wire && wire !== defaultNullWire()));
      const selectedInputWires = new Set(Object.values(selected.inputs || {}).filter((wire) => wire && wire !== defaultNullWire()));
      const instanceOutputWires = Object.values(instance.outputs || {}).filter((wire) => wire && wire !== defaultNullWire());
      const instanceInputWires = Object.values(instance.inputs || {}).filter((wire) => wire && wire !== defaultNullWire());
      return instanceInputWires.some((wire) => selectedOutputWires.has(wire))
        || instanceOutputWires.some((wire) => selectedInputWires.has(wire));
    }

    function nodeIsRecentlyConnected(instanceId) {
      if (!lastConnectedPair || Date.now() > lastConnectedUntil) return false;
      return lastConnectedPair.sourceId === instanceId || lastConnectedPair.targetId === instanceId;
    }

    function visiblePortNames(instance) {
      const fullInputs = orderedPortNames(instance?.ports, "inputs");
      const fullOutputs = orderedPortNames(instance?.ports, "outputs");
      const isSelected = selectedNodeId === instance.instanceId;
      const isPendingSource = pendingConnection?.instanceId === instance.instanceId;
      if (isPendingSource) {
        const connectedInputs = fullInputs.filter((name) => instance.inputs?.[name] && instance.inputs?.[name] !== defaultNullWire());
        return {
          inputs: connectedInputs.length ? connectedInputs : fullInputs.slice(0, 1),
          outputs: pendingConnection?.portName ? [pendingConnection.portName] : fullOutputs.slice(0, 1),
        };
      }
      if (isSelected) {
        return { inputs: fullInputs, outputs: fullOutputs };
      }
      if (pendingConnection) {
        const connectedInputs = fullInputs.filter((name) => instance.inputs?.[name] && instance.inputs?.[name] !== defaultNullWire());
        return {
          inputs: fullInputs.length ? fullInputs : connectedInputs,
          outputs: consumerPortNames(instance),
        };
      }
      const connectedInputs = fullInputs.filter((name) => instance.inputs?.[name] && instance.inputs?.[name] !== defaultNullWire());
      const connectedOutputs = consumerPortNames(instance);
      const defaultOutputs = connectedOutputs.length
        ? connectedOutputs
        : (fullInputs.length ? [] : fullOutputs.slice(0, 1));
      return {
        inputs: connectedInputs.length ? connectedInputs : fullInputs.slice(0, 1),
        outputs: defaultOutputs,
      };
    }

    function createPortButton(instance, direction, portName, active, options = {}) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `alpha-port alpha-port-${direction}`;
      if (options.primary) button.classList.add("is-primary");
      if (options.connected) button.classList.add("is-connected-port");
      if (direction === "output" && pendingConnection?.instanceId === instance.instanceId && pendingConnection?.portName === portName) {
        button.classList.add("is-armed");
      }
      if (direction === "input" && pendingConnection) {
        button.classList.add("is-accepting");
      }
      if (active) button.classList.add("is-active");
      button.dataset.instanceId = instance.instanceId;
      button.dataset.portName = portName;
      button.dataset.portDirection = direction;
      button.innerHTML = `
        <span class="alpha-port-dot" aria-hidden="true"></span>
        <span class="alpha-port-label">${escapeHtml(compactPortLabel(portName))}</span>
      `;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (direction === "output") {
          beginConnection(instance.instanceId, portName);
          return;
        }
        if (pendingConnection) {
          completeConnection(instance.instanceId, portName);
          return;
        }
        selectedNodeId = instance.instanceId;
        render();
      });
      return button;
    }

    function createNode(instance) {
      const module = moduleById.get(instance.moduleId);
      const visiblePorts = visiblePortNames(instance);
      const inputNames = visiblePorts.inputs;
      const outputNames = visiblePorts.outputs;
      const expanded = selectedNodeId === instance.instanceId || pendingConnection?.instanceId === instance.instanceId || !!pendingConnection;
      const denseExpandedLayout = expanded && useDenseExpandedLayout(inputNames, outputNames);
      const pendingSourceSingleOutput = pendingConnection?.instanceId === instance.instanceId && !inputNames.length && outputNames.length === 1;
      const pendingTargetSingleInput = !!pendingConnection && pendingConnection.instanceId !== instance.instanceId && inputNames.length === 1 && !outputNames.length;
      const metric = nodeMetrics(instance, visiblePorts.inputs, visiblePorts.outputs, {
        pendingSourceSingleOutput,
        pendingTargetSingleInput,
      });
      const pos = resolvedNodePosition(instance.instanceId, metric);
      const rowCount = Math.max(1, inputNames.length, outputNames.length);
      const ioPanelExpanded = expanded && !denseExpandedLayout && inputNames.length === 1 && outputNames.length === 1;
      const outputsOnlyExpanded = denseExpandedLayout && !inputNames.length && outputNames.length > 1;
      const inputsOnlyExpanded = denseExpandedLayout && inputNames.length > 1 && !outputNames.length;
      const node = document.createElement("article");
      node.className = "alpha-canvas-node";
      if (selectedNodeId === instance.instanceId) node.classList.add("selected");
      if (nodeIsRelatedToSelected(instance)) node.classList.add("alpha-canvas-node-related");
      if (nodeIsRecentlyConnected(instance.instanceId)) node.classList.add("alpha-canvas-node-just-connected");
      if (pendingConnection?.instanceId === instance.instanceId) node.classList.add("alpha-canvas-node-connecting");
      if (instanceHasConnectedInput(instance)) node.classList.add("alpha-canvas-node-has-input");
      if (instanceHasConnectedOutput(instance)) node.classList.add("alpha-canvas-node-has-output");
      if (denseExpandedLayout) node.classList.add("alpha-canvas-node-dense");
      if (outputsOnlyExpanded) node.classList.add("alpha-canvas-node-outputs-only");
      if (inputsOnlyExpanded) node.classList.add("alpha-canvas-node-inputs-only");
      if (pendingSourceSingleOutput) node.classList.add("alpha-canvas-node-source-single");
      if (pendingTargetSingleInput) node.classList.add("alpha-canvas-node-input-single");
      if (ioPanelExpanded) node.classList.add("alpha-canvas-node-io-panel");
      node.dataset.instanceId = instance.instanceId;
      node.style.left = `${pos.x}px`;
      node.style.top = `${pos.y}px`;
      node.style.width = `${metric.width}px`;
      node.style.height = `${metric.height}px`;

      node.innerHTML = `
        <div class="alpha-node-card">
          <div class="alpha-node-head">
            <strong class="alpha-node-title">${escapeHtml(moduleDisplayName(module))}</strong>
            <button type="button" class="bp-node-delete" aria-label="Delete node" data-delete-node-inline="${escapeHtml(instance.instanceId)}"></button>
          </div>
          <div class="alpha-node-body alpha-node-port-rows"></div>
        </div>
      `;

      node.querySelector("[data-delete-node-inline]")?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeNode(instance.instanceId);
      });

      const rowsRoot = node.querySelector(".alpha-node-port-rows");
      if (pendingSourceSingleOutput) {
        rowsRoot.classList.add("alpha-node-source-panel");
        const panelLabel = document.createElement("div");
        panelLabel.className = "alpha-node-source-panel-label";
        panelLabel.textContent = "OUT";
        const primaryGroup = document.createElement("div");
        primaryGroup.className = "alpha-node-output-primary";
        primaryGroup.appendChild(createPortButton(instance, "output", outputNames[0], true, {
          primary: true,
          connected: outputPortIsConnected(instance, outputNames[0]),
        }));
        rowsRoot.appendChild(panelLabel);
        rowsRoot.appendChild(primaryGroup);
      } else if (pendingTargetSingleInput) {
        rowsRoot.classList.add("alpha-node-input-panel");
        const panelLabel = document.createElement("div");
        panelLabel.className = "alpha-node-source-panel-label alpha-node-input-panel-label";
        panelLabel.textContent = "IN";
        const primaryGroup = document.createElement("div");
        primaryGroup.className = "alpha-node-input-primary";
        primaryGroup.appendChild(createPortButton(
          instance,
          "input",
          inputNames[0],
          instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
          {
            primary: true,
            connected: instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
          },
        ));
        rowsRoot.appendChild(panelLabel);
        rowsRoot.appendChild(primaryGroup);
      } else if (ioPanelExpanded) {
        rowsRoot.classList.add("alpha-node-io-panel");
        const inputPanel = document.createElement("div");
        inputPanel.className = "alpha-node-io-side alpha-node-io-side-input";
        const inputLabel = document.createElement("div");
        inputLabel.className = "alpha-node-source-panel-label alpha-node-input-panel-label";
        inputLabel.textContent = "IN";
        if (inputNames[0] && instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire()) {
          inputPanel.classList.add("is-connected-side");
        }
        inputPanel.appendChild(inputLabel);
        inputPanel.appendChild(createPortButton(
          instance,
          "input",
          inputNames[0],
          instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
          {
            primary: true,
            connected: instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
          },
        ));
        const outputPanel = document.createElement("div");
        outputPanel.className = "alpha-node-io-side alpha-node-io-side-output";
        const outputLabel = document.createElement("div");
        outputLabel.className = "alpha-node-source-panel-label";
        outputLabel.textContent = "OUT";
        if (outputNames[0] && outputPortIsConnected(instance, outputNames[0])) {
          outputPanel.classList.add("is-connected-side");
        }
        outputPanel.appendChild(outputLabel);
        outputPanel.appendChild(createPortButton(instance, "output", outputNames[0], true, {
          primary: true,
          connected: outputPortIsConnected(instance, outputNames[0]),
        }));
        rowsRoot.appendChild(inputPanel);
        rowsRoot.appendChild(outputPanel);
      } else if (denseExpandedLayout) {
        if (outputsOnlyExpanded) {
          rowsRoot.classList.add("alpha-node-source-panel");
          const panelLabel = document.createElement("div");
          panelLabel.className = "alpha-node-source-panel-label";
          panelLabel.textContent = "OUT";
          const primaryGroup = document.createElement("div");
          primaryGroup.className = "alpha-node-output-primary";
          const primaryLabel = document.createElement("div");
          primaryLabel.className = "alpha-node-output-group-label";
          primaryLabel.textContent = "PRIMARY";
          if (outputNames[0]) {
            primaryGroup.appendChild(createPortButton(instance, "output", outputNames[0], true, {
              primary: true,
              connected: outputPortIsConnected(instance, outputNames[0]),
            }));
          }
          const secondaryGroup = document.createElement("div");
          secondaryGroup.className = "alpha-node-output-secondary";
          const secondaryLabel = document.createElement("div");
          secondaryLabel.className = "alpha-node-output-group-label alpha-node-output-group-label-secondary";
          secondaryLabel.textContent = "MORE";
          outputNames.slice(1).forEach((outputName) => {
            secondaryGroup.appendChild(createPortButton(instance, "output", outputName, true, {
              connected: outputPortIsConnected(instance, outputName),
            }));
          });
          rowsRoot.appendChild(panelLabel);
          rowsRoot.appendChild(primaryLabel);
          rowsRoot.appendChild(primaryGroup);
          if (outputNames.length > 1) rowsRoot.appendChild(secondaryLabel);
          rowsRoot.appendChild(secondaryGroup);
        } else if (inputsOnlyExpanded) {
          rowsRoot.classList.add("alpha-node-input-panel");
          const panelLabel = document.createElement("div");
          panelLabel.className = "alpha-node-source-panel-label alpha-node-input-panel-label";
          panelLabel.textContent = "IN";
          const primaryLabel = document.createElement("div");
          primaryLabel.className = "alpha-node-output-group-label alpha-node-input-group-label";
          primaryLabel.textContent = "PRIMARY";
          const primaryGroup = document.createElement("div");
          primaryGroup.className = "alpha-node-input-primary";
          if (inputNames[0]) {
            primaryGroup.appendChild(createPortButton(
              instance,
              "input",
              inputNames[0],
              instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
              {
                primary: true,
                connected: instance.inputs?.[inputNames[0]] && instance.inputs?.[inputNames[0]] !== defaultNullWire(),
              },
            ));
          }
          const secondaryGroup = document.createElement("div");
          secondaryGroup.className = "alpha-node-input-secondary";
          const secondaryLabel = document.createElement("div");
          secondaryLabel.className = "alpha-node-output-group-label alpha-node-output-group-label-secondary alpha-node-input-group-label";
          secondaryLabel.textContent = "MORE";
          inputNames.slice(1).forEach((inputName) => {
            secondaryGroup.appendChild(createPortButton(
              instance,
              "input",
              inputName,
              instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              {
                connected: instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              },
            ));
          });
          rowsRoot.appendChild(panelLabel);
          rowsRoot.appendChild(primaryLabel);
          rowsRoot.appendChild(primaryGroup);
          if (inputNames.length > 1) rowsRoot.appendChild(secondaryLabel);
          rowsRoot.appendChild(secondaryGroup);
        } else {
          rowsRoot.classList.add("alpha-node-port-groups");
          const inputGroup = document.createElement("div");
          inputGroup.className = "alpha-node-port-group alpha-node-port-group-input";
          inputNames.forEach((inputName) => {
            inputGroup.appendChild(createPortButton(
              instance,
              "input",
              inputName,
              instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              {
                connected: instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              },
            ));
          });
          const outputGroup = document.createElement("div");
          outputGroup.className = "alpha-node-port-group alpha-node-port-group-output";
          outputNames.forEach((outputName, outputIndex) => {
            outputGroup.appendChild(createPortButton(instance, "output", outputName, true, { primary: outputIndex === 0 }));
          });
          rowsRoot.appendChild(inputGroup);
          rowsRoot.appendChild(outputGroup);
        }
      } else {
        for (let index = 0; index < rowCount; index += 1) {
          const row = document.createElement("div");
          row.className = "alpha-node-port-row";
          const inputName = inputNames[index];
          const outputName = outputNames[index];
          if (inputName) {
            row.appendChild(createPortButton(
              instance,
              "input",
              inputName,
              instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              {
                connected: instance.inputs?.[inputName] && instance.inputs?.[inputName] !== defaultNullWire(),
              },
            ));
          } else {
            const spacer = document.createElement("span");
            spacer.className = "alpha-port-spacer";
            row.appendChild(spacer);
          }
          const gap = document.createElement("span");
          gap.className = "alpha-port-row-gap";
          gap.setAttribute("aria-hidden", "true");
          row.appendChild(gap);
          if (outputName) {
            row.appendChild(createPortButton(instance, "output", outputName, true, {
              connected: outputPortIsConnected(instance, outputName),
            }));
          } else {
            const spacer = document.createElement("span");
            spacer.className = "alpha-port-spacer";
            row.appendChild(spacer);
          }
          rowsRoot.appendChild(row);
        }
      }

      node.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        if (event.target.closest("button,input,select,textarea")) return;
        event.preventDefault();
        event.stopPropagation();
        selectedNodeId = instance.instanceId;
        draggingNode = {
          instanceId: instance.instanceId,
          startX: event.clientX,
          startY: event.clientY,
          originX: pos.x,
          originY: pos.y,
          width: metric.width,
          height: metric.height,
          moved: false,
        };
      });

      node.addEventListener("click", (event) => {
        event.stopPropagation();
        selectedNodeId = instance.instanceId;
        render();
      });

      node.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        showMenu(event.clientX, event.clientY, instance.instanceId);
      });

      return node;
    }

    function resolvePortPoint(instanceId, direction, portName) {
      const node = surface.querySelector(`.alpha-canvas-node[data-instance-id="${CSS.escape(instanceId)}"]`);
      if (!node) return null;
      const port = node.querySelector(`.alpha-port[data-port-direction="${CSS.escape(direction)}"][data-port-name="${CSS.escape(portName)}"]`);
      const viewportRect = viewport.getBoundingClientRect();
      if (port) {
        const dot = port.querySelector(".alpha-port-dot");
        const rect = (dot || port).getBoundingClientRect();
        return {
          x: direction === "input" ? rect.left - viewportRect.left : rect.right - viewportRect.left,
          y: rect.top - viewportRect.top + rect.height / 2,
        };
      }
      const rect = node.getBoundingClientRect();
      return {
        x: direction === "input" ? rect.left - viewportRect.left : rect.right - viewportRect.left,
        y: rect.top - viewportRect.top + rect.height / 2,
      };
    }

    function edgePathData(from, to) {
      const startX = Math.round(from.x);
      const startY = Math.round(from.y);
      const endX = Math.round(to.x);
      const endY = Math.round(to.y);
      const delta = Math.max(48, Math.abs(endX - startX) * 0.45);
      return `M ${startX} ${startY} C ${startX + delta} ${startY}, ${endX - delta} ${endY}, ${endX} ${endY}`;
    }

    function edgeMarkup(from, to, edgeClass = "alpha-graph-edge", shadowClass = "alpha-graph-edge-shadow") {
      const path = edgePathData(from, to);
      return `
        <path class="${shadowClass}" d="${path}"></path>
        <path class="${edgeClass}" d="${path}"></path>
      `;
    }

    function edgeLabelMarkup(from, to, sourcePortName, inputName) {
      const midX = Math.round((from.x + to.x) / 2);
      const midY = Math.round((from.y + to.y) / 2);
      const text = `${compactPortLabel(sourcePortName)} -> ${compactPortLabel(inputName)}`;
      const width = Math.max(44, text.length * 6 + 10);
      const x = Math.round(midX - width / 2);
      const y = Math.round(midY - 10);
      return `
        <g class="alpha-edge-label" transform="translate(${x} ${y})">
          <rect class="alpha-edge-label-box" width="${width}" height="20" rx="10" ry="10"></rect>
          <text class="alpha-edge-label-text" x="${Math.round(width / 2)}" y="13">${escapeHtml(text)}</text>
        </g>
      `;
    }

    function renderEdges() {
      const producerByWire = buildProducerMap(state.instances);
      const defs = `
        <defs>
          <marker id="alpha-edge-arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse">
            <path d="M 0 0 L 10 6 L 0 12 z" fill="#0f172a"></path>
          </marker>
          <marker id="alpha-edge-preview-arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse">
            <path d="M 0 0 L 10 6 L 0 12 z" fill="#d9480f"></path>
          </marker>
        </defs>
      `;
      const paths = [];
      const labels = [];

      Object.values(state.instances).forEach((instance) => {
        Object.entries(instance.inputs || {}).forEach(([inputName, wire]) => {
          if (!wire || wire === defaultNullWire()) return;
          const source = producerByWire.get(wire);
          if (!source) return;
          const from = resolvePortPoint(source.instanceId, "output", source.portName);
          const to = resolvePortPoint(instance.instanceId, "input", inputName);
          if (!from || !to) return;
          const isFocused = selectedNodeId && (selectedNodeId === source.instanceId || selectedNodeId === instance.instanceId);
          const hitPath = edgePathData(from, to);
          paths.push(edgeMarkup(from, to));
          paths.push(
            `<path class="alpha-graph-edge-hit" data-source-instance="${escapeHtml(source.instanceId)}" data-target-instance="${escapeHtml(instance.instanceId)}" d="${hitPath}"></path>`,
          );
          if (isFocused) {
            paths.push(edgeMarkup(from, to, "alpha-graph-edge-focus", "alpha-graph-edge-focus-shadow"));
          }
          labels.push(edgeLabelMarkup(from, to, source.portName, inputName));
        });
      });

      if (pendingConnection) {
        const from = resolvePortPoint(pendingConnection.instanceId, "output", pendingConnection.portName);
        const targets = [...surface.querySelectorAll('.alpha-port[data-port-direction="input"]')];
        if (from) {
          targets.forEach((target) => {
            const rect = target.getBoundingClientRect();
            const viewportRect = viewport.getBoundingClientRect();
            const to = {
              x: rect.left - viewportRect.left,
              y: rect.top - viewportRect.top + rect.height / 2,
            };
            paths.push(edgeMarkup(from, to, "alpha-graph-edge-preview", "alpha-graph-edge-preview-shadow"));
          });
        }
      }

      svg.innerHTML = defs + paths.join("") + labels.join("");
    }

    function renderMenu(x, y, nodeInstanceId = "") {
      menu.hidden = false;
      menu.style.left = `${x}px`;
      menu.style.top = `${y}px`;
      menu.innerHTML = `
        <div class="alpha-blueprint-menu-card">
          <div class="alpha-blueprint-menu-title">${nodeInstanceId ? "Node Actions" : "Add Signal Node"}</div>
          ${nodeInstanceId
            ? `<button type="button" data-delete-node="${nodeInstanceId}">Delete ${escapeHtml(moduleDisplayName(moduleById.get(state.instances[nodeInstanceId]?.moduleId) || { moduleId: nodeInstanceId }))}</button>`
            : menuModules.map((module) => `
                <button type="button" data-add-node="${module.moduleId}">
                  <span class="alpha-menu-item-title">${escapeHtml(moduleDisplayName(module))}</span>
                </button>
              `).join("")
          }
        </div>
      `;
      menu.querySelectorAll("[data-add-node]").forEach((button) => {
        button.addEventListener("click", () => {
          addNode(button.dataset.addNode, menu.__menuPoint || { x: STAGE_PADDING, y: STAGE_PADDING });
          hideMenu();
        });
      });
      menu.querySelectorAll("[data-delete-node]").forEach((button) => {
        button.addEventListener("click", () => {
          removeNode(button.dataset.deleteNode);
          hideMenu();
        });
      });
    }

    function hideMenu() {
      menu.hidden = true;
      menu.innerHTML = "";
    }

    function clearSelection() {
      if (!selectedNodeId && !pendingConnection) return;
      selectedNodeId = "";
      pendingConnection = null;
      render();
    }

    function showMenu(clientX, clientY, nodeInstanceId = "") {
      const stageRect = stage.getBoundingClientRect();
      menu.__menuPoint = {
        x: Math.round(clientX - stageRect.left + stage.scrollLeft),
        y: Math.round(clientY - stageRect.top + stage.scrollTop),
      };
      renderMenu(clientX, clientY, nodeInstanceId);
    }

    function beginConnection(instanceId, portName) {
      selectedNodeId = instanceId;
      pendingConnection = { instanceId, portName };
      render();
    }

    function completeConnection(targetInstanceId, inputName) {
      if (!pendingConnection) return;
      const target = state.instances[targetInstanceId];
      const source = state.instances[pendingConnection.instanceId];
      if (!target || !source || target.instanceId === source.instanceId) {
        clearSelection();
        return;
      }
      const wire = source.outputs?.[pendingConnection.portName] || defaultNullWire();
      target.inputs[inputName] = wire;
      if (graphHasCycle(state.instances)) {
        target.inputs[inputName] = defaultNullWire();
      }
      lastConnectedPair = { sourceId: source.instanceId, targetId: target.instanceId };
      lastConnectedUntil = Date.now() + 1200;
      if (lastConnectedTimer) clearTimeout(lastConnectedTimer);
      lastConnectedTimer = window.setTimeout(() => {
        lastConnectedTimer = 0;
        if (lastConnectedPair && Date.now() > lastConnectedUntil) {
          lastConnectedPair = null;
          render();
        }
      }, 1250);
      pendingConnection = null;
      selectedNodeId = "";
      emitChange();
      render();
    }

    function removeNode(instanceId) {
      const producerMap = buildProducerMap(state.instances);
      delete state.instances[instanceId];
      state.nodeIds = state.nodeIds.filter((id) => id !== instanceId);
      delete state.positions[instanceId];
      if (selectedNodeId === instanceId) selectedNodeId = "";
      Object.values(state.instances).forEach((instance) => {
        Object.entries(instance.inputs || {}).forEach(([name, wire]) => {
          const producer = producerMap.get(wire);
          if (producer?.instanceId === instanceId) instance.inputs[name] = defaultNullWire();
        });
      });
      emitChange();
      render();
    }

    function clearAllNodes() {
      Object.keys(state.positions).forEach((key) => delete state.positions[key]);
      Object.keys(state.instances).forEach((instanceId) => delete state.instances[instanceId]);
      state.nodeIds = [];
      selectedNodeId = "";
      pendingConnection = null;
      emitChange();
      render();
    }

    function addNode(moduleId, preferred) {
      const module = moduleById.get(moduleId);
      if (!module) return;
      const instanceId = `${moduleId}.${Date.now().toString(36)}`;
      const instance = {
        instanceId,
        moduleId,
        version: module.version,
        kind: module.kind,
        config: forms.schemaDefaults(module.configSchema || {}),
        inputs: Object.fromEntries(orderedPortNames(module.ports, "inputs").map((name) => [name, defaultNullWire()])),
        outputs: Object.fromEntries(
          orderedPortNames(module.ports, "outputs").map((name) => [name, uniqueWireName(state, defaultOutputKey(moduleId, name))]),
        ),
        ports: module.ports || { inputs: {}, outputs: {} },
      };
      state.instances[instanceId] = instance;
      state.nodeIds.push(instanceId);
      const visiblePorts = visiblePortNames(instance);
      const metric = nodeMetrics(instance, visiblePorts.inputs, visiblePorts.outputs);
      state.positions[instanceId] = resolvedNodePosition(instanceId, metric, preferred);
      autoLayout = !preferred && state.nodeIds.length <= 2;
      selectedNodeId = instanceId;
      emitChange();
      render();
    }

    function reframeView() {
      stage.scrollLeft = 0;
      stage.scrollTop = 0;
    }

    function refreshLayout() {
      if (rafToken) cancelAnimationFrame(rafToken);
      rafToken = requestAnimationFrame(() => {
        rafToken = 0;
        if (shouldAutoLayoutNow()) {
          relayoutDefaultNodes();
        } else {
          clampAllNodePositions();
        }
        render();
      });
    }

    function render() {
      shell.__debugLastError = null;
      try {
        layoutViewport();
        if (shouldAutoLayoutNow()) {
          relayoutDefaultNodes();
        } else if (!Object.keys(state.positions).length) {
          relayoutDefaultNodes();
        }
        surface.innerHTML = "";
        state.nodeIds.filter((id) => state.instances[id]).forEach((instanceId) => {
          surface.appendChild(createNode(state.instances[instanceId]));
        });
        renderEdges();
      stage.scrollLeft = 0;
      stage.scrollTop = 0;
      if (lastConnectedPair && Date.now() > lastConnectedUntil) {
        lastConnectedPair = null;
      }
    } catch (error) {
        shell.__debugLastError = { message: String(error), stack: error?.stack || null };
        throw error;
      }
    }

    function handleStageContextMenu(event) {
      event.preventDefault();
      const node = event.target.closest(".alpha-canvas-node");
      showMenu(event.clientX, event.clientY, node?.dataset.instanceId || "");
    }

    function handleStageClick(event) {
      const edgeHit = event.target.closest(".alpha-graph-edge-hit");
      if (edgeHit) {
        selectedNodeId = edgeHit.getAttribute("data-source-instance") || "";
        render();
        return;
      }
      const nodeAtPoint = [...surface.querySelectorAll(".alpha-canvas-node")].reverse().find((node) => {
        const rect = node.getBoundingClientRect();
        return event.clientX >= rect.left
          && event.clientX <= rect.right
          && event.clientY >= rect.top
          && event.clientY <= rect.bottom;
      });
      if (nodeAtPoint) {
        selectedNodeId = nodeAtPoint.dataset.instanceId || "";
        render();
        return;
      }
      if (event.target.closest(".alpha-canvas-node, .alpha-port, .bp-node-delete")) return;
      if (![stage, viewport, surface, svg].includes(event.target)) return;
      clearSelection();
    }

    function handleWindowMouseDown(event) {
      if (!event.target.closest(".alpha-blueprint-menu-card")) hideMenu();
    }

    function handleWindowMouseMove(event) {
      if (!draggingNode) return;
      draggingNode.moved = true;
      autoLayout = false;
      const bounds = stageBounds();
      const x = Math.max(STAGE_PADDING, Math.min(
        draggingNode.originX + (event.clientX - draggingNode.startX),
        bounds.width - draggingNode.width - STAGE_PADDING,
      ));
      const y = Math.max(STAGE_PADDING, Math.min(
        draggingNode.originY + (event.clientY - draggingNode.startY),
        bounds.height - draggingNode.height - STAGE_PADDING,
      ));
      state.positions[draggingNode.instanceId] = { x, y };
      render();
    }

    function handleWindowMouseUp() {
      if (!draggingNode) return;
      if (!draggingNode.moved) {
        selectedNodeId = draggingNode.instanceId;
        draggingNode = null;
        render();
        return;
      }
      draggingNode = null;
      emitChange();
      render();
    }

    function handleWindowKeyDown(event) {
      const target = event.target;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return;
      if (event.key === "Escape") {
        event.preventDefault();
        clearSelection();
        hideMenu();
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedNodeId && state.instances[selectedNodeId]) {
        event.preventDefault();
        removeNode(selectedNodeId);
      }
    }

    function cleanup() {
      if (rafToken) {
        cancelAnimationFrame(rafToken);
        rafToken = 0;
      }
      if (lastConnectedTimer) {
        clearTimeout(lastConnectedTimer);
        lastConnectedTimer = 0;
      }
      stage.removeEventListener("contextmenu", handleStageContextMenu);
      stage.removeEventListener("click", handleStageClick);
      window.removeEventListener("mousedown", handleWindowMouseDown);
      window.removeEventListener("mousemove", handleWindowMouseMove);
      window.removeEventListener("mouseup", handleWindowMouseUp);
      window.removeEventListener("keydown", handleWindowKeyDown);
      window.removeEventListener("resize", refreshLayout);
      if (root.__alphaBlueprintCleanup === cleanup) delete root.__alphaBlueprintCleanup;
    }

    stage.addEventListener("contextmenu", handleStageContextMenu);
    stage.addEventListener("click", handleStageClick);
    window.addEventListener("mousedown", handleWindowMouseDown);
    window.addEventListener("mousemove", handleWindowMouseMove);
    window.addEventListener("mouseup", handleWindowMouseUp);
    window.addEventListener("keydown", handleWindowKeyDown);
    window.addEventListener("resize", refreshLayout);

    shell.__debugAddNode = addNode;
    shell.__debugClearNodes = clearAllNodes;
    shell.__debugMoveNode = (instanceId, x, y) => {
      autoLayout = false;
      const visiblePorts = visiblePortNames(state.instances[instanceId] || { ports: { inputs: {}, outputs: {} } });
      const metric = nodeMetrics(state.instances[instanceId] || {}, visiblePorts.inputs, visiblePorts.outputs);
      state.positions[instanceId] = resolvedNodePosition(instanceId, metric, { x, y });
      render();
    };
    shell.__debugSelection = () => ({ selectedNodeId, pendingConnection });
    shell.__refreshLayout = refreshLayout;
    shell.__reframeView = reframeView;
    shell.__cleanup = cleanup;
    root.__alphaBlueprintCleanup = cleanup;

    render();
    return { refreshLayout, reframeView, cleanup };
  }

  window.AlphaBlueprint = { mount };
}());
