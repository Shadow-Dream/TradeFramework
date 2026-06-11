(function () {
  const forms = window.TradeModuleForms;
  const X6 = window.X6;

  const STAGE_PADDING = 48;
  const NODE_WIDTH = 168;
  const NODE_HEIGHT = 72;
  const EXPANDED_NODE_HEIGHT = 94;
  const DEFAULT_CHAIN_PAIR_GAP = 140;
  const DEFAULT_CHAIN_TOP_RATIO = 0.46;

  function defaultNullWire() {
    return "null";
  }

  function orderedPortNames(ports, direction) {
    return Object.keys(ports?.[direction] || {});
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

  function moduleDisplayName(module) {
    const compactNames = {
      "price-source": "Price",
      "sma-indicator": "SMA",
    };
    return compactNames[module?.moduleId || ""] || forms.humanizeName(module?.moduleId || "node");
  }

  function defaultOutputKey(moduleId, portName) {
    return `${moduleId}.${portName}`;
  }

  function uniqueWireName(state, preferred) {
    const used = new Set();
    Object.values(state.instances).forEach((instance) => {
      Object.values(instance.outputs || {}).forEach((wire) => {
        if (wire) used.add(wire);
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

  function serializeAlphaGraph(state) {
    return { nodes: [...state.nodeIds], outputs: {} };
  }

  function nodeHeightForState({ selected = false, pendingSource = false, pendingTarget = false } = {}) {
    if (selected) return EXPANDED_NODE_HEIGHT;
    if (pendingSource || pendingTarget) return 80;
    return NODE_HEIGHT;
  }

  function primaryInputPortName(instance) {
    return orderedPortNames(instance?.ports, "inputs")[0] || "";
  }

  function primaryOutputPortName(instance) {
    return orderedPortNames(instance?.ports, "outputs")[0] || "";
  }

  function defaultNodePosition(bounds, count, index) {
    const totalWidth = count * NODE_WIDTH + Math.max(0, count - 1) * DEFAULT_CHAIN_PAIR_GAP;
    const startX = Math.max(STAGE_PADDING, Math.round((bounds.width - totalWidth) / 2));
    const startY = Math.max(STAGE_PADDING, Math.round(bounds.height * DEFAULT_CHAIN_TOP_RATIO));
    return {
      x: startX + index * (NODE_WIDTH + DEFAULT_CHAIN_PAIR_GAP),
      y: startY,
    };
  }

  function consumerPortNames(state, instance) {
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

  function visiblePortNames(state, instance, selectedNodeId) {
    const fullInputs = orderedPortNames(instance?.ports, "inputs");
    const fullOutputs = orderedPortNames(instance?.ports, "outputs");
    if (selectedNodeId === instance.instanceId) {
      return { inputs: fullInputs, outputs: fullOutputs };
    }
    const connectedInputs = fullInputs.filter((name) => instance.inputs?.[name] && instance.inputs?.[name] !== defaultNullWire());
    const connectedOutputs = consumerPortNames(state, instance);
    return {
      inputs: connectedInputs.length ? connectedInputs : fullInputs.slice(0, 1),
      outputs: connectedOutputs.length ? connectedOutputs : (fullInputs.length ? [] : fullOutputs.slice(0, 1)),
    };
  }

  function outputPortIsConnected(state, instance, portName) {
    const wire = instance.outputs?.[portName];
    if (!wire || wire === defaultNullWire()) return false;
    return Object.values(state.instances).some((other) => (
      other.instanceId !== instance.instanceId
      && Object.values(other.inputs || {}).some((inputWire) => inputWire === wire)
    ));
  }

  function nodeIsRelatedToSelected(state, instance, selectedNodeId) {
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

  function instanceHasConnectedInput(instance) {
    return Object.values(instance.inputs || {}).some((wire) => wire && wire !== defaultNullWire());
  }

  function instanceHasConnectedOutput(state, instance) {
    return Object.keys(instance.outputs || {}).some((portName) => outputPortIsConnected(state, instance, portName));
  }

  function capturePositions(graph, state) {
    graph.getNodes().forEach((node) => {
      state.positions[node.id] = {
        x: Math.round(node.position().x),
        y: Math.round(node.position().y),
      };
    });
  }

  function mount(options) {
    const { root, modules, instances, alphaGraph, onChange } = options;
    if (!root || !X6?.Graph) return null;
    root.__alphaBlueprintCleanup?.();

    root.innerHTML = '<div class="alpha-x6-root"><div class="alpha-x6-canvas"></div></div>';
    const canvas = root.querySelector(".alpha-x6-canvas");
    const moduleById = new Map(modules.map((module) => [module.moduleId, module]));
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
    let suppressSelection = false;
    let autoLayout = true;
    let pendingVisualSourceId = "";
    let visualClassFrame = 0;

    function currentVisiblePortNames(instance) {
      const fullInputs = orderedPortNames(instance?.ports, "inputs");
      const fullOutputs = orderedPortNames(instance?.ports, "outputs");
      if (pendingVisualSourceId === instance.instanceId) {
        const primaryOutput = primaryOutputPortName(instance);
        return {
          inputs: [],
          outputs: primaryOutput ? [primaryOutput] : fullOutputs.slice(0, 1),
        };
      }
      if (pendingVisualSourceId && pendingVisualSourceId !== instance.instanceId) {
        const primaryInput = primaryInputPortName(instance);
        return {
          inputs: primaryInput ? [primaryInput] : fullInputs.slice(0, 1),
          outputs: [],
        };
      }
      return visiblePortNames(state, instance, selectedNodeId);
    }

    const graph = new X6.Graph({
      container: canvas,
      grid: { visible: true, size: 28 },
      panning: false,
      mousewheel: false,
      interacting: {
        edgeMovable: false,
        vertexMovable: false,
      },
      background: { color: "#f8fbfc" },
      connecting: {
        allowBlank: false,
        allowLoop: false,
        allowNode: false,
        snap: true,
        connector: { name: "smooth" },
        createEdge() {
          return graph.createEdge({
            attrs: {
              line: {
                stroke: "#d9480f",
                strokeWidth: 5,
                strokeDasharray: "10 8",
                targetMarker: { name: "classic", size: 8 },
              },
            },
          });
        },
        validateConnection({ sourceCell, targetCell, sourcePort, targetPort }) {
          if (!sourceCell || !targetCell) return false;
          if (!sourcePort || !targetPort) return false;
          if (sourceCell.id === targetCell.id) return false;
          return sourcePort.startsWith("out:") && targetPort.startsWith("in:");
        },
      },
    });

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

    function buildNodeCell(instanceId, index, bounds) {
      const instance = state.instances[instanceId];
      const module = moduleById.get(instance.moduleId);
      const visiblePorts = currentVisiblePortNames(instance);
      const selected = selectedNodeId === instanceId;
      const related = nodeIsRelatedToSelected(state, instance, selectedNodeId);
      const pendingSource = pendingVisualSourceId === instanceId;
      const pendingTarget = !!pendingVisualSourceId && pendingVisualSourceId !== instanceId;
      const pos = autoLayout
        ? defaultNodePosition(bounds, state.nodeIds.length, index)
        : (state.positions[instanceId] || defaultNodePosition(bounds, state.nodeIds.length, index));
      const hasInputs = visiblePorts.inputs.length > 0;
      const hasOutputs = visiblePorts.outputs.length > 0;
      const selectedSource = (selected || pendingSource) && !hasInputs && visiblePorts.outputs.length > 0;
      const selectedTarget = selected && hasInputs && hasOutputs;
      const hidePortLabels = pendingSource || pendingTarget;
      const showInputPanel = selectedTarget || pendingTarget;
      const showOutputPanel = selectedSource || selectedTarget;
      const connectedInput = hasInputs && instance.inputs?.[primaryInputPortName(instance)] && instance.inputs?.[primaryInputPortName(instance)] !== defaultNullWire();
      const connectedOutput = hasOutputs && outputPortIsConnected(state, instance, primaryOutputPortName(instance));

      return graph.createNode({
        id: instanceId,
        x: pos.x,
        y: pos.y,
        width: NODE_WIDTH,
        height: nodeHeightForState({ selected, pendingSource, pendingTarget }),
        markup: [
          { tagName: "rect", selector: "body" },
          { tagName: "text", selector: "label" },
          { tagName: "rect", selector: "leftPanel" },
          { tagName: "text", selector: "leftPanelLabel" },
          { tagName: "rect", selector: "rightPanel" },
          { tagName: "text", selector: "rightPanelLabel" },
        ],
        attrs: {
          body: {
            fill: "#ffffff",
            stroke: pendingSource
              ? "#d9480f"
              : pendingTarget
                ? "rgba(217,72,15,0.45)"
                : selected
                  ? "#0f766e"
                  : (related ? "rgba(15,118,110,0.45)" : "rgba(17,24,39,0.14)"),
            strokeWidth: (selected || pendingSource) ? 2 : ((related || pendingTarget) ? 2 : 1),
            rx: 14,
            ry: 14,
          },
          label: {
            text: moduleDisplayName(module),
            fill: "#0f172a",
            fontSize: 12,
            fontWeight: 700,
            refX: 12,
            refY: 18,
            textAnchor: "start",
          },
          leftPanel: {
            width: showInputPanel ? 46 : 0,
            height: showInputPanel ? 54 : 0,
            x: 0,
            y: 28,
            fill: showInputPanel
              ? (pendingTarget ? "rgba(217,72,15,0.08)" : (connectedInput ? "rgba(15,118,110,0.12)" : "rgba(15,118,110,0.05)"))
              : "transparent",
            stroke: showInputPanel
              ? (pendingTarget ? "rgba(217,72,15,0.18)" : "rgba(15,23,42,0.08)")
              : (related && connectedInput ? "rgba(15,118,110,0.18)" : "transparent"),
            rx: 10,
            ry: 10,
          },
          leftPanelLabel: {
            text: showInputPanel ? "IN" : " ",
            fill: "#6b7280",
            fontSize: 7,
            fontWeight: 700,
            refX: 12,
            refY: 42,
            textAnchor: "start",
          },
          rightPanel: {
            width: showOutputPanel ? 58 : 0,
            height: showOutputPanel ? 54 : 0,
            x: showOutputPanel ? NODE_WIDTH - 70 : 0,
            y: 28,
            fill: showOutputPanel
              ? (pendingSource ? "rgba(217,72,15,0.08)" : (connectedOutput ? "rgba(15,118,110,0.12)" : "rgba(15,118,110,0.05)"))
              : (related && connectedOutput ? "rgba(15,118,110,0.08)" : "transparent"),
            stroke: showOutputPanel
              ? (pendingSource ? "rgba(217,72,15,0.18)" : "rgba(15,23,42,0.08)")
              : (related && connectedOutput ? "rgba(15,118,110,0.18)" : "transparent"),
            rx: 10,
            ry: 10,
          },
          rightPanelLabel: {
            text: showOutputPanel ? "OUT" : " ",
            fill: "#6b7280",
            fontSize: 7,
            fontWeight: 700,
            refX: showOutputPanel ? NODE_WIDTH - 18 : 0,
            refY: 42,
            textAnchor: "end",
          },
        },
        ports: {
          groups: {
            in: {
              position: "left",
              attrs: {
                circle: {
                  magnet: true,
                  r: 5,
                  stroke: "#0f766e",
                  strokeWidth: 2,
                  fill: "#ffffff",
                },
                text: {
                  text: hidePortLabels ? "" : undefined,
                  fontSize: 8,
                  fill: "#0f172a",
                },
              },
            },
            out: {
              position: "right",
              attrs: {
                circle: {
                  magnet: true,
                  r: 5,
                  stroke: "#0f766e",
                  strokeWidth: 2,
                  fill: "#ffffff",
                },
                text: {
                  text: hidePortLabels ? "" : undefined,
                  fontSize: 8,
                  fill: "#0f172a",
                },
              },
            },
          },
          items: [
            ...visiblePorts.inputs.map((name) => ({
              id: `in:${name}`,
              group: "in",
              attrs: { text: { text: compactPortLabel(name) } },
            })),
            ...visiblePorts.outputs.map((name) => ({
              id: `out:${name}`,
              group: "out",
              attrs: { text: { text: compactPortLabel(name) } },
            })),
          ],
        },
      });
    }

    function buildEdgeCell(instance, inputName, source) {
      const text = `${compactPortLabel(source.portName)} -> ${compactPortLabel(inputName)}`;
      const focused = selectedNodeId && (selectedNodeId === source.instanceId || selectedNodeId === instance.instanceId);
      return graph.createEdge({
        id: `${source.instanceId}:${source.portName}->${instance.instanceId}:${inputName}`,
        source: { cell: source.instanceId, port: `out:${source.portName}` },
        target: { cell: instance.instanceId, port: `in:${inputName}` },
        markup: [
          { tagName: "path", selector: "outline" },
          { tagName: "path", selector: "line" },
        ],
        attrs: {
          outline: {
            connection: true,
            stroke: "rgba(255,255,255,0.96)",
            strokeWidth: focused ? 13 : 11,
            strokeLinecap: "round",
          },
          line: {
            stroke: focused ? "#0f766e" : "#0f172a",
            strokeWidth: focused ? 7 : 6,
            targetMarker: { name: "classic", size: 8 },
          },
        },
        labels: [
          {
            attrs: {
              body: {
                fill: focused ? "rgba(223,243,239,0.98)" : "rgba(255,255,255,0.96)",
                stroke: focused ? "rgba(15,118,110,0.24)" : "rgba(15,23,42,0.12)",
                rx: 10,
                ry: 10,
              },
              label: {
                text,
                fill: focused ? "#0f766e" : "#0f172a",
                fontSize: 8,
                fontWeight: 700,
              },
            },
            position: 0.5,
          },
        ],
      });
    }

    function renderGraph() {
      suppressSelection = true;
      const bounds = {
        width: canvas.clientWidth || root.clientWidth || 1400,
        height: canvas.clientHeight || root.clientHeight || 920,
      };
      const producerByWire = buildProducerMap(state.instances);
      const cells = [];
      state.nodeIds.forEach((instanceId, index) => {
        if (!state.instances[instanceId]) return;
        cells.push(buildNodeCell(instanceId, index, bounds));
      });
      Object.values(state.instances).forEach((instance) => {
        Object.entries(instance.inputs || {}).forEach(([inputName, wire]) => {
          if (!wire || wire === defaultNullWire()) return;
          const source = producerByWire.get(wire);
          if (!source) return;
          cells.push(buildEdgeCell(instance, inputName, source));
        });
      });
      graph.resetCells(cells);
      suppressSelection = false;
      if (visualClassFrame) cancelAnimationFrame(visualClassFrame);
      visualClassFrame = requestAnimationFrame(() => {
        visualClassFrame = 0;
        syncVisualClasses();
      });
    }

    function syncVisualClasses() {
      canvas.querySelectorAll('.x6-node').forEach((nodeEl) => {
        const cellId = nodeEl.getAttribute('data-cell-id');
        const instance = state.instances[cellId];
        if (!instance) return;
        const classes = [
          'x6-cell',
          'x6-node',
          selectedNodeId === cellId ? 'x6-selected-node' : '',
          nodeIsRelatedToSelected(state, instance, selectedNodeId) ? 'x6-related-node' : '',
          instanceHasConnectedInput(instance) ? 'x6-node-has-input' : '',
          instanceHasConnectedOutput(state, instance) ? 'x6-node-has-output' : '',
          (!!pendingVisualSourceId && cellId === pendingVisualSourceId) ? 'x6-connecting-source' : '',
          (!!pendingVisualSourceId && !!cellId && cellId !== pendingVisualSourceId) ? 'x6-connecting-target' : '',
        ].filter(Boolean).join(' ');
        nodeEl.setAttribute('class', classes);
      });

      canvas.querySelectorAll('.x6-edge').forEach((edgeEl) => {
        const cellId = edgeEl.getAttribute('data-cell-id');
        const edge = graph.getCellById(cellId);
        if (!edge) return;
        const sourceId = edge.getSourceCellId();
        const targetId = edge.getTargetCellId();
        const focused = !!selectedNodeId && (selectedNodeId === sourceId || selectedNodeId === targetId);
        edgeEl.setAttribute('class', ['x6-cell', 'x6-edge', focused ? 'x6-focused-edge' : ''].filter(Boolean).join(' '));
      });
    }

    function syncPendingVisuals() {
      renderGraph();
    }

    function refreshLayout() {
      graph.resize(canvas.clientWidth || root.clientWidth || 1400, canvas.clientHeight || root.clientHeight || 920);
      renderGraph();
    }

    graph.on("node:click", ({ node }) => {
      selectedNodeId = node.id;
      capturePositions(graph, state);
      renderGraph();
    });

    graph.on("edge:click", ({ edge }) => {
      const sourceId = edge.getSourceCellId();
      if (!sourceId) return;
      selectedNodeId = sourceId;
      renderGraph();
    });

    graph.on("blank:click", () => {
      selectedNodeId = "";
      renderGraph();
    });

    graph.on("node:moved", ({ node }) => {
      autoLayout = false;
      state.positions[node.id] = {
        x: Math.round(node.position().x),
        y: Math.round(node.position().y),
      };
      emitChange();
    });

    graph.on("edge:connected", ({ edge, isNew }) => {
      const sourceId = edge.getSourceCellId();
      const targetId = edge.getTargetCellId();
      const sourcePort = edge.getSourcePortId();
      const targetPort = edge.getTargetPortId();
      if (!sourceId || !targetId || !sourcePort || !targetPort) {
        if (isNew) edge.remove();
        return;
      }
      const source = state.instances[sourceId];
      const target = state.instances[targetId];
      if (!source || !target) {
        if (isNew) edge.remove();
        return;
      }
      const sourcePortName = sourcePort.replace(/^out:/, "");
      const inputName = targetPort.replace(/^in:/, "");
      const wire = source.outputs?.[sourcePortName] || defaultNullWire();
      target.inputs[inputName] = wire;
      if (graphHasCycle(state.instances)) {
        target.inputs[inputName] = defaultNullWire();
      }
      pendingVisualSourceId = "";
      selectedNodeId = "";
      capturePositions(graph, state);
      emitChange();
      renderGraph();
    });

    function beginPendingVisual(nodeId, portId) {
      if (!portId.startsWith('out:')) return;
      pendingVisualSourceId = nodeId || '';
      syncPendingVisuals();
    }

    graph.on('node:port:mousedown', ({ node, port }) => {
      const portId = typeof port === 'string' ? port : (port?.id || '');
      beginPendingVisual(node?.id || '', portId);
    });

    function handlePortPointerDown(event) {
      const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
      const portEl = path.find((node) => node?.getAttribute?.('port')) || event.target?.closest?.('[port]');
      if (!portEl) return;
      const portId = portEl.getAttribute('port') || '';
      const nodeEl = (path.find((node) => node?.getAttribute?.('data-cell-id')) || portEl.closest?.('.x6-node'));
      beginPendingVisual(nodeEl?.getAttribute('data-cell-id') || '', portId);
    }

    root.addEventListener('mousedown', handlePortPointerDown, true);

    function handlePendingMouseUp() {
      if (!pendingVisualSourceId) return;
      pendingVisualSourceId = "";
      syncPendingVisuals();
    }

    window.addEventListener('mouseup', handlePendingMouseUp);

    function handleResize() {
      if (autoLayout) {
        refreshLayout();
        return;
      }
      graph.resize(canvas.clientWidth || root.clientWidth || 1400, canvas.clientHeight || root.clientHeight || 920);
    }

    renderGraph();
    root.__x6Graph = graph;
    root.__x6State = state;
    root.__x6Render = renderGraph;
    root.__refreshLayout = refreshLayout;
    root.__x6Selection = () => selectedNodeId;
    root.__x6PendingSource = () => pendingVisualSourceId;
    root.__alphaBlueprintCleanup = () => {
      if (visualClassFrame) {
        cancelAnimationFrame(visualClassFrame);
        visualClassFrame = 0;
      }
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('mouseup', handlePendingMouseUp);
      root.removeEventListener('mousedown', handlePortPointerDown, true);
      graph.dispose();
    };
    window.addEventListener('resize', handleResize);
    return { cleanup: root.__alphaBlueprintCleanup };
  }

  window.AlphaBlueprintX6 = { mount };
}());
