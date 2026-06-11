(function () {
  function resolvePath(root, path) {
    if (!path) return root;
    if (root && typeof root === "object" && Object.prototype.hasOwnProperty.call(root, path)) {
      return root[path];
    }
    return String(path).split(".").reduce((node, key) => (node ? node[key] : undefined), root);
  }

  function fieldValue(row, field, fallback) {
    if (field === undefined || field === null || field === "") return fallback;
    const value = String(field).split(".").reduce((node, key) => (node ? node[key] : undefined), row);
    return value === undefined ? fallback : value;
  }

  function chartTime(value) {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? value : Math.floor(parsed / 1000);
  }

  function sortByTime(rows) {
    return rows
      .filter((row) => row.time !== undefined && row.time !== null)
      .sort((a, b) => (typeof a.time === "number" && typeof b.time === "number" ? a.time - b.time : String(a.time).localeCompare(String(b.time))));
  }

  function layerId(layer) {
    return layer.id || `${layer.callback || layer.renderer || "visualizer"}:${JSON.stringify(layer.params || {})}`;
  }

  function normalizeVisualizationSpec(result, spec) {
    const next = { schemaVersion: 2, ...(spec || {}) };
    next.dataKeys = { ...(result?.dataKeys || {}), ...(next.dataKeys || {}) };
    next.panes = (next.panes || []).map((pane, paneIndex) => ({
      ...pane,
      role: pane.role || pane.type || "line",
      visualizers: (pane.visualizers || []).map((item, itemIndex) => ({
        visible: true,
        params: {},
        ...item,
        id: item.id || `${item.callback || item.renderer || "visualizer"}.${paneIndex + 1}.${itemIndex + 1}`,
      })),
      temporaryModules: [...(pane.temporaryModules || [])],
    }));
    return next;
  }

  function dataKeyDeclarations(result, spec = {}) {
    const declared = { ...(result?.dataKeys || {}), ...(spec?.dataKeys || {}) };
    const temporary = {};
    for (const module of spec?.temporaryModules || []) {
      for (const [portName, dataKey] of Object.entries(module?.outputs || {})) {
        if (!dataKey) continue;
        temporary[dataKey] = {
          label: dataKey,
          type: module?.ports?.outputs?.[portName]?.type || "any",
          path: dataKey,
          source: { path: dataKey },
          encoding: { time: "date", value: "value" },
          paneRole: "line",
          module: {
            source: "temporary",
            instanceId: module.instanceId,
            kind: module.kind,
            moduleId: module.moduleId,
            version: module.version,
            config: module.config || {},
            output: portName,
          },
        };
      }
    }
    return { ...declared, ...temporary };
  }

  function layerFromDataKey(key, declaration) {
    return {
      id: key,
      dataKey: key,
      label: declaration.label || key,
      renderer: declaration.defaultDrawCallback || declaration.renderer || declaration.defaultRenderer || "series.line",
      target: declaration.target || declaration.defaultTarget,
      source: declaration.source || { path: declaration.path || key },
      encoding: declaration.encoding || {},
      style: declaration.style || {},
      paneRole: declaration.paneRole || declaration.role || "line",
      required: declaration.required === true,
      dataType: declaration.type || "any",
    };
  }

  function chartLayerCatalog(result, spec = {}) {
    return Object.entries(dataKeyDeclarations(result, spec)).map(([key, declaration]) => layerFromDataKey(key, declaration));
  }

  function isType(dataType, prefix) {
    return String(dataType || "").toLowerCase().startsWith(prefix.toLowerCase());
  }

  function dataKeyOptions(result, spec, predicate = null) {
    return chartLayerCatalog(result, spec)
      .filter((item) => !predicate || predicate(item))
      .map((item) => ({ value: item.dataKey, label: item.label || item.dataKey, dataType: item.dataType, paneRole: item.paneRole }))
      .sort((a, b) => String(a.label).localeCompare(String(b.label)));
  }

  function visualizerCatalog(result, spec) {
    return [
      {
        id: "ohlc.candles",
        label: "Candles",
        params: [
          { name: "priceDataKey", label: "Price Data", type: "dataKey" },
          { name: "upColor", label: "Up Color", type: "string", default: "#089981" },
          { name: "downColor", label: "Down Color", type: "string", default: "#f23645" },
        ],
        optionMap: {
          priceDataKey: dataKeyOptions(result, spec, (item) => isType(item.dataType, "ohlcv")),
        },
      },
      {
        id: "series.line",
        label: "Line",
        params: [
          { name: "dataKey", label: "Data", type: "dataKey" },
          { name: "color", label: "Color", type: "string", default: "#2563eb" },
          { name: "lineWidth", label: "Width", type: "number", default: 2, min: 1 },
        ],
        optionMap: {
          dataKey: dataKeyOptions(result, spec, (item) => !isType(item.dataType, "ohlcv") && !isType(item.dataType, "event.marker")),
        },
      },
      {
        id: "series.scatter",
        label: "Scatter",
        params: [
          { name: "dataKey", label: "Data", type: "dataKey" },
          { name: "color", label: "Color", type: "string", default: "#2563eb" },
          { name: "pointRadius", label: "Radius", type: "number", default: 3, min: 1 },
        ],
        optionMap: {
          dataKey: dataKeyOptions(result, spec, (item) => !isType(item.dataType, "ohlcv") && !isType(item.dataType, "event.marker")),
        },
      },
      {
        id: "series.histogram",
        label: "Histogram",
        params: [
          { name: "dataKey", label: "Data", type: "dataKey" },
          { name: "color", label: "Color", type: "string", default: "#64748b" },
          { name: "positiveColor", label: "Positive", type: "string", default: "#089981" },
          { name: "negativeColor", label: "Negative", type: "string", default: "#f23645" },
        ],
        optionMap: {
          dataKey: dataKeyOptions(result, spec, (item) => !isType(item.dataType, "ohlcv") && !isType(item.dataType, "event.marker")),
        },
      },
      {
        id: "overlay.markers",
        label: "Markers",
        params: [
          { name: "dataKey", label: "Marker Data", type: "dataKey" },
          { name: "targetDataKey", label: "Target Data", type: "dataKey" },
        ],
        optionMap: {
          dataKey: dataKeyOptions(result, spec, (item) => isType(item.dataType, "event.marker")),
          targetDataKey: dataKeyOptions(result, spec, (item) => isType(item.dataType, "ohlcv") || !isType(item.dataType, "event.marker")),
        },
      },
      {
        id: "overlay.priceLine",
        label: "Price Line",
        params: [
          { name: "dataKey", label: "Data", type: "dataKey" },
          { name: "color", label: "Color", type: "string", default: "#475569" },
          { name: "lineWidth", label: "Width", type: "number", default: 1, min: 1 },
        ],
        optionMap: {
          dataKey: dataKeyOptions(result, spec, (item) => !isType(item.dataType, "ohlcv") && !isType(item.dataType, "event.marker")),
        },
      },
    ];
  }

  function layerFromVisualizerInstance(result, spec, instance) {
    const callback = instance?.callback || instance?.renderer;
    const params = { ...(instance?.params || {}) };
    const declarations = dataKeyDeclarations(result, spec);
    if (callback === "ohlc.candles") {
      const dataKey = params.priceDataKey;
      const declaration = declarations[dataKey];
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      return { ...base, id: instance.id || dataKey, dataKey, renderer: callback, params, visible: instance.visible !== false };
    }
    if (callback === "series.line" || callback === "series.scatter" || callback === "series.histogram" || callback === "overlay.priceLine") {
      const dataKey = params.dataKey;
      const declaration = declarations[dataKey];
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      return { ...base, id: instance.id || dataKey, dataKey, renderer: callback, params, visible: instance.visible !== false };
    }
    if (callback === "overlay.markers") {
      const dataKey = params.dataKey;
      const targetDataKey = params.targetDataKey;
      const declaration = declarations[dataKey];
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      return {
        ...base,
        id: instance.id || dataKey,
        dataKey,
        renderer: callback,
        target: targetDataKey,
        params,
        visible: instance.visible !== false,
      };
    }
    return null;
  }

  function collectPaneSourcePaths(result, pane, spec = {}) {
    const declarations = dataKeyDeclarations(result, spec);
    const paths = new Set();
    const layers = (pane?.visualizers || []).map((item) => layerFromVisualizerInstance(result, spec, item)).filter(Boolean);
    const activeKeys = new Set();
    for (const layer of layers) {
      activeKeys.add(layer.dataKey);
      if (layer.source?.path) paths.add(layer.source.path);
    }
    for (const module of spec?.temporaryModules || []) {
      const outputs = Object.values(module?.outputs || {});
      if (!outputs.some((dataKey) => activeKeys.has(dataKey))) continue;
      for (const dataKey of Object.values(module?.inputs || {})) {
        if (!dataKey) continue;
        const declaration = declarations[dataKey];
        const sourcePath = declaration?.source?.path || declaration?.path;
        if (sourcePath) paths.add(sourcePath);
      }
    }
    return [...paths];
  }

  function addChartSeries(library, chart, family, options) {
    if (family === "candlestick" && chart.addCandlestickSeries) return chart.addCandlestickSeries(options);
    if (family === "line" && chart.addLineSeries) return chart.addLineSeries(options);
    if (family === "histogram" && chart.addHistogramSeries) return chart.addHistogramSeries(options);
    const seriesType = family === "candlestick"
      ? library.CandlestickSeries
      : family === "histogram"
        ? library.HistogramSeries
        : library.LineSeries;
    return chart.addSeries(seriesType, options);
  }

  function createFinancialChart(container, options = {}) {
    return window.LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { color: "#ffffff" },
        textColor: "#172026",
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "#eef2f4" },
        horzLines: { color: "#eef2f4" },
      },
      rightPriceScale: {
        borderColor: "#d9e0e4",
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "#d9e0e4",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: window.LightweightCharts.CrosshairMode?.Normal ?? 0,
        vertLine: { color: "#63717a", labelBackgroundColor: "#0f766e" },
        horzLine: { color: "#63717a", labelBackgroundColor: "#0f766e" },
      },
      localization: {
        priceFormatter: (price) => Number(price).toLocaleString(undefined, { maximumFractionDigits: 4 }),
      },
      ...options,
    });
  }

  function rendererRows(result, layer) {
    const source = layer.source || {};
    if (Array.isArray(source.data)) return source.data;
    const rows = resolvePath(result, source.path);
    return Array.isArray(rows) ? rows : [];
  }

  function lineStyle(library, value) {
    if (typeof value === "number") return value;
    const key = String(value || "solid").toLowerCase();
    if (key === "dashed") return library.LineStyle?.Dashed ?? 2;
    if (key === "dotted") return library.LineStyle?.Dotted ?? 1;
    return library.LineStyle?.Solid ?? 0;
  }

  const renderers = {};
  const rendererLabels = {};
  const rendererParams = {};

  function registerRenderer(id, renderer) {
    renderers[id] = renderer;
    rendererLabels[id] = renderer.label || id;
    rendererParams[id] = renderer.params || [];
  }

  function registerDrawCallback(id, callback) {
    registerRenderer(id, callback);
  }

  function drawCallbackCatalog() {
    return Object.keys(renderers).sort().map((id) => ({
      id,
      label: rendererLabels[id] || id,
      params: rendererParams[id] || [],
    }));
  }

  function rememberSeries(ctx, layer, series) {
    ctx.seriesByLayerId.set(layerId(layer), series);
    if (layer.dataKey && !ctx.seriesByDataKey.has(layer.dataKey)) {
      ctx.seriesByDataKey.set(layer.dataKey, series);
    }
  }

  registerRenderer("ohlc.candles", {
    label: "Candles",
    overlay: false,
    params: [
      { name: "upColor", label: "Up", type: "text", default: "#089981" },
      { name: "downColor", label: "Down", type: "text", default: "#f23645" },
    ],
    draw(ctx, layer) {
      const enc = layer.encoding || {};
      const style = { ...(layer.style || {}), ...(layer.params || {}) };
      const series = addChartSeries(ctx.library, ctx.chart, "candlestick", {
        upColor: style.upColor || "#089981",
        downColor: style.downColor || "#f23645",
        borderUpColor: style.borderUpColor || style.upColor || "#089981",
        borderDownColor: style.borderDownColor || style.downColor || "#f23645",
        wickUpColor: style.wickUpColor || style.upColor || "#089981",
        wickDownColor: style.wickDownColor || style.downColor || "#f23645",
      });
      const rows = rendererRows(ctx.result, layer).map((row) => ({
        time: chartTime(fieldValue(row, enc.time || "date")),
        open: fieldValue(row, enc.open || "open"),
        high: fieldValue(row, enc.high || "high"),
        low: fieldValue(row, enc.low || "low"),
        close: fieldValue(row, enc.close || "close"),
      }));
      series.setData(sortByTime(rows.filter((row) => (
        typeof row.open === "number" &&
        typeof row.high === "number" &&
        typeof row.low === "number" &&
        typeof row.close === "number"
      ))));
      ctx.primarySeries ||= series;
      rememberSeries(ctx, layer, series);
      return series;
    },
  });

  registerRenderer("series.line", {
    label: "Line",
    overlay: false,
    params: [
      { name: "color", label: "Color", type: "text", default: "#2563eb" },
      { name: "lineWidth", label: "Width", type: "number", min: 1, step: 1, default: 2 },
    ],
    draw(ctx, layer) {
      const enc = layer.encoding || {};
      const style = { ...(layer.style || {}), ...(layer.params || {}) };
      const series = addChartSeries(ctx.library, ctx.chart, "line", {
        color: style.color || "#2563eb",
        lineWidth: style.lineWidth || (ctx.pane.role === "ohlcv" ? 2 : 3),
        lineStyle: lineStyle(ctx.library, style.lineStyle),
        priceLineVisible: style.priceLineVisible ?? false,
        lastValueVisible: style.lastValueVisible ?? true,
      });
      const valueField = enc.value || enc.close || "value";
      const rows = rendererRows(ctx.result, layer)
        .filter((row) => typeof fieldValue(row, valueField) === "number")
        .map((row) => ({ time: chartTime(fieldValue(row, enc.time || "date")), value: fieldValue(row, valueField) }));
      series.setData(sortByTime(rows));
      ctx.primarySeries ||= series;
      rememberSeries(ctx, layer, series);
      return series;
    },
  });

  registerRenderer("series.scatter", {
    label: "Scatter",
    overlay: false,
    params: [
      { name: "color", label: "Color", type: "text", default: "#2563eb" },
      { name: "pointRadius", label: "Radius", type: "number", min: 1, step: 1, default: 3 },
    ],
    draw(ctx, layer) {
      const enc = layer.encoding || {};
      const style = { ...(layer.style || {}), ...(layer.params || {}) };
      const series = addChartSeries(ctx.library, ctx.chart, "line", {
        color: style.color || "#2563eb",
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: style.pointRadius || 3,
        priceLineVisible: style.priceLineVisible ?? false,
        lastValueVisible: style.lastValueVisible ?? true,
      });
      const valueField = enc.value || enc.close || "value";
      const rows = rendererRows(ctx.result, layer)
        .filter((row) => typeof fieldValue(row, valueField) === "number")
        .map((row) => ({ time: chartTime(fieldValue(row, enc.time || "date")), value: fieldValue(row, valueField) }));
      series.setData(sortByTime(rows));
      ctx.primarySeries ||= series;
      rememberSeries(ctx, layer, series);
      return series;
    },
  });

  registerRenderer("series.histogram", {
    label: "Histogram",
    overlay: false,
    params: [
      { name: "color", label: "Color", type: "text", default: "#64748b" },
      { name: "positiveColor", label: "Positive", type: "text", default: "#089981" },
      { name: "negativeColor", label: "Negative", type: "text", default: "#f23645" },
    ],
    draw(ctx, layer) {
      const enc = layer.encoding || {};
      const style = { ...(layer.style || {}), ...(layer.params || {}) };
      const series = addChartSeries(ctx.library, ctx.chart, "histogram", {
        color: style.color || "#64748b",
        priceLineVisible: style.priceLineVisible ?? false,
        lastValueVisible: style.lastValueVisible ?? true,
      });
      const valueField = enc.value || "value";
      const rows = rendererRows(ctx.result, layer)
        .filter((row) => typeof fieldValue(row, valueField) === "number")
        .map((row) => {
          const value = fieldValue(row, valueField);
          return {
            time: chartTime(fieldValue(row, enc.time || "date")),
            value,
            color: fieldValue(row, enc.color, value >= 0 ? (style.positiveColor || style.color || "#089981") : (style.negativeColor || "#f23645")),
          };
        });
      series.setData(sortByTime(rows));
      ctx.primarySeries ||= series;
      rememberSeries(ctx, layer, series);
      return series;
    },
  });

  registerRenderer("overlay.markers", {
    label: "Markers",
    overlay: true,
    draw(ctx, layer) {
      const target = ctx.seriesByLayerId.get(layer.target) || ctx.seriesByDataKey.get(layer.target) || ctx.primarySeries;
      if (!target) return null;
      const enc = layer.encoding || {};
      const style = layer.style || {};
      const markers = rendererRows(ctx.result, layer).map((row) => {
        const side = String(fieldValue(row, enc.side || "type", "default")).toLowerCase();
        const picked = style[side] || style.default || {};
        return {
          time: chartTime(fieldValue(row, enc.time || "date")),
          position: fieldValue(row, enc.position, picked.position || "aboveBar"),
          color: fieldValue(row, enc.color, picked.color || "#475569"),
          shape: fieldValue(row, enc.shape, picked.shape || "circle"),
          text: fieldValue(row, enc.text, side),
        };
      });
      const sorted = sortByTime(markers);
      if (typeof ctx.library.createSeriesMarkers === "function") return ctx.library.createSeriesMarkers(target, sorted);
      if (typeof target.setMarkers === "function") target.setMarkers(sorted);
      return null;
    },
  });

  registerRenderer("overlay.priceLine", {
    label: "Price Line",
    overlay: true,
    draw(ctx, layer) {
      const target = ctx.seriesByLayerId.get(layer.target) || ctx.seriesByDataKey.get(layer.target) || ctx.primarySeries;
      if (!target?.createPriceLine) return null;
      const rows = rendererRows(ctx.result, layer);
      const enc = layer.encoding || {};
      const style = { ...(layer.style || {}), ...(layer.params || {}) };
      const value = fieldValue(rows[rows.length - 1] || {}, enc.value || "value");
      if (typeof value !== "number") return null;
      return target.createPriceLine({
        price: value,
        color: style.color || "#475569",
        lineWidth: style.lineWidth || 1,
        lineStyle: lineStyle(ctx.library, style.lineStyle),
        axisLabelVisible: style.axisLabelVisible ?? true,
        title: style.title || layer.label || layer.id || "",
      });
    },
  });

  function drawFinancialPane(library, chart, result, pane, spec = {}) {
    const layers = (pane.visualizers || [])
      .map((instance) => layerFromVisualizerInstance(result, spec, instance))
      .filter((layer) => layer && layer.visible !== false);
    const ctx = { library, chart, result, pane, spec, seriesByLayerId: new Map(), seriesByDataKey: new Map(), primarySeries: null };
    layers.filter((layer) => !renderers[layer.renderer]?.overlay).forEach((layer) => renderers[layer.renderer]?.draw(ctx, layer));
    layers.filter((layer) => renderers[layer.renderer]?.overlay).forEach((layer) => renderers[layer.renderer]?.draw(ctx, layer));
    return ctx;
  }

  function synchronizeTimeScales(charts) {
    let syncing = false;
    charts.forEach((chart) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (syncing || !range) return;
        syncing = true;
        charts.forEach((candidate) => {
          if (candidate !== chart) candidate.timeScale().setVisibleLogicalRange(range);
        });
        syncing = false;
      });
    });
  }

  window.TradeChartCore = {
    chartLayerCatalog,
    createFinancialChart,
    dataKeyDeclarations,
    drawCallbackCatalog,
    drawFinancialPane,
    visualizerCatalog,
    collectPaneSourcePaths,
    layerId,
    normalizeVisualizationSpec,
    registerDrawCallback,
    registerRenderer,
    synchronizeTimeScales,
  };
}());
