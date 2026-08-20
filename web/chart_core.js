(function () {
  function resolvePath(root, path) {
    if (!path) return root;
    return String(path).split(".").reduce((node, key) => (node ? node[key] : undefined), root);
  }

  function fieldValue(row, field, fallback) {
    if (field === undefined || field === null || field === "") return fallback;
    const value = resolvePath(row, field);
    return value === undefined ? fallback : value;
  }

  function chartTime(value) {
    if (typeof value === "number") return value;
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? value : Math.floor(parsed / 1000);
  }

  function chartDateFromTime(value) {
    if (typeof value === "number") return new Date(value * 1000);
    if (typeof value === "string") {
      const parsed = Date.parse(value);
      if (!Number.isNaN(parsed)) return new Date(parsed);
    }
    if (value && typeof value === "object" && value.year && value.month && value.day) {
      return new Date(Date.UTC(value.year, value.month - 1, value.day));
    }
    return null;
  }

  function datePartsInZone(value, timeZone, includeSeconds = false) {
    const date = chartDateFromTime(value);
    if (!date) return null;
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      ...(includeSeconds ? { second: "2-digit" } : {}),
      hourCycle: "h23",
    });
    return Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  }

  function formatChartClock(value, options = {}) {
    const displayZone = options.showTime ? (options.timeZone || "UTC") : "UTC";
    const parts = datePartsInZone(value, displayZone);
    if (!parts) return String(value ?? "");
    const year = String(parts.year || "").slice(-2);
    if (!options.showTime) return `${parts.month}/${parts.day}/${year}`;
    return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
  }

  function formatRangeInput(value, options = {}) {
    if (value === undefined || value === null || value === "") return "";
    const displayZone = options.showTime ? (options.timeZone || "UTC") : "UTC";
    const parts = datePartsInZone(value, displayZone, true);
    if (!parts) return "";
    const date = `${parts.year}-${parts.month}-${parts.day}`;
    return options.showTime ? `${date}T${parts.hour}:${parts.minute}` : date;
  }

  function parseZonedDateTime(value, timeZone) {
    const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
    if (!match) return null;
    const desired = {
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
      hour: Number(match[4]),
      minute: Number(match[5]),
      second: Number(match[6] || 0),
    };
    const desiredUtc = Date.UTC(desired.year, desired.month - 1, desired.day, desired.hour, desired.minute, desired.second);
    let guess = desiredUtc;
    for (let index = 0; index < 4; index += 1) {
      const parts = datePartsInZone(guess / 1000, timeZone, true);
      if (!parts) return null;
      const represented = Date.UTC(
        Number(parts.year), Number(parts.month) - 1, Number(parts.day),
        Number(parts.hour), Number(parts.minute), Number(parts.second || 0),
      );
      guess += desiredUtc - represented;
    }
    const verified = datePartsInZone(guess / 1000, timeZone, true);
    if (!verified || ["year", "month", "day", "hour", "minute", "second"].some((key) => Number(verified[key] || 0) !== desired[key])) {
      return null;
    }
    return Math.floor(guess / 1000);
  }

  function parseRangeInput(value, options = {}) {
    const text = String(value || "").trim();
    if (!text) return null;
    if (!options.showTime) {
      const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (!match) return Number.NaN;
      const parsed = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
      const date = new Date(parsed);
      if (date.getUTCFullYear() !== Number(match[1]) || date.getUTCMonth() !== Number(match[2]) - 1 || date.getUTCDate() !== Number(match[3])) return Number.NaN;
      return Math.floor(parsed / 1000);
    }
    const parsed = parseZonedDateTime(text, options.timeZone || "UTC");
    return parsed === null ? Number.NaN : parsed;
  }

  function sortByTime(rows) {
    return rows
      .filter((row) => row.time !== undefined && row.time !== null)
      .sort((a, b) => (typeof a.time === "number" && typeof b.time === "number" ? a.time - b.time : String(a.time).localeCompare(String(b.time))));
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function validChartTime(value) {
    if (finiteNumber(value)) return true;
    if (typeof value === "string" && value.trim()) return !Number.isNaN(Date.parse(value));
    return !!(value && typeof value === "object" && value.year && value.month && value.day);
  }

  function sparseLinePoints(rows, encoding = {}) {
    const valueField = encoding.value || encoding.close || "value";
    return sortByTime((rows || []).flatMap((row) => {
      const value = fieldValue(row, valueField);
      const time = chartTime(fieldValue(row, encoding.time || "date"));
      return finiteNumber(value) && validChartTime(time) ? [{ time, value }] : [];
    }));
  }

  function completeCandlePoints(rows, encoding = {}) {
    return sortByTime((rows || []).flatMap((row) => {
      if (fieldValue(row, encoding.complete || "complete") === false) return [];
      const candle = {
        time: chartTime(fieldValue(row, encoding.time || "date")),
        open: fieldValue(row, encoding.open || "open"),
        high: fieldValue(row, encoding.high || "high"),
        low: fieldValue(row, encoding.low || "low"),
        close: fieldValue(row, encoding.close || "close"),
      };
      return validChartTime(candle.time) && [candle.open, candle.high, candle.low, candle.close].every(finiteNumber)
        ? [candle]
        : [];
    }));
  }

  function layerId(layer) {
    return layer.id || `${layer.callback}:${JSON.stringify(layer.params || {})}`;
  }

  function normalizeVisualizationSpec(result, spec) {
    if (spec?.schemaVersion !== 3) throw new Error("Visualization schemaVersion 3 is required");
    if (typeof spec.timeZone !== "string" || !spec.timeZone.trim()) throw new Error("Visualization timeZone is required");
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: spec.timeZone }).format(0);
    } catch {
      throw new Error("Visualization timeZone must be a valid IANA time zone");
    }
    if (!Array.isArray(spec.panes)) throw new Error("Visualization panes must be an array");
    return structuredClone(spec);
  }

  function createTemporaryModuleInstance(definition, values) {
    if (!definition || !values?.instanceId) throw new Error("Temporary Module identity is required");
    return {
      instanceId: String(values.instanceId),
      kind: definition.kind,
      moduleId: definition.moduleId,
      version: definition.version,
      config: structuredClone(values.config || {}),
      inputs: structuredClone(values.inputs || {}),
      outputs: structuredClone(values.outputs || {}),
    };
  }

  function createVisualizerInstance(definition, values) {
    if (!definition || !values?.id) throw new Error("Visualizer identity is required");
    const params = structuredClone(values.params || {});
    const missing = (definition.params || []).filter((field) => (
      field.required && (params[field.name] === undefined || params[field.name] === "")
    ));
    if (missing.length) {
      throw new Error(`Missing visualizer params: ${missing.map((field) => field.label || field.name).join(", ")}`);
    }
    return { id: String(values.id), callback: definition.id, params };
  }

  function upsertIdentity(items, currentId, nextItem, identityField) {
    const values = Array.isArray(items) ? items : [];
    if (!currentId) return [...values, nextItem];
    return values.map((item) => item?.[identityField] === currentId ? nextItem : item);
  }

  function dataKeyDeclarations(result, spec = {}) {
    const declared = { ...(result?.dataKeys || {}) };
    const temporary = {};
    for (const module of spec?.temporaryModules || []) {
      const definition = temporaryModuleDefinition(module);
      for (const [portName, dataKey] of Object.entries(module?.outputs || {})) {
        if (!dataKey) continue;
        const parts = String(dataKey).split(".");
        for (let index = 1; index < parts.length; index += 1) {
          const parent = parts.slice(0, index).join(".");
          temporary[parent] ||= {
            label: parent,
            schema: { type: "object" },
            source: { path: `cycles.data.${parent}` },
            encoding: { time: "decisionTime", value: `data.${parent}` },
          };
        }
        temporary[dataKey] = {
          label: dataKey,
          schema: definition?.ports?.outputs?.[portName]?.schema || {},
          source: { path: `cycles.data.${dataKey}` },
          encoding: { time: "decisionTime", value: `data.${dataKey}` },
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
    const expanded = { ...declared, ...temporary };
    function addProperties(path, declaration) {
      const schema = normalizeSchema(declaration.schema);
      for (const [name, childSchema] of Object.entries(schema.properties || {})) {
        const childPath = `${path}.${name}`;
        expanded[childPath] ||= {
          ...declaration,
          label: childPath,
          schema: childSchema,
          encoding: {
            ...(declaration.encoding || {}),
            value: `${declaration.encoding?.value || `data.${path}`}.${name}`,
          },
        };
        addProperties(childPath, expanded[childPath]);
      }
    }
    Object.entries({ ...expanded }).forEach(([path, declaration]) => addProperties(path, declaration));
    return expanded;
  }

  function literalSchema(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const properties = Object.fromEntries(
        Object.entries(value).map(([name, child]) => [name, literalSchema(child)]),
      );
      return {
        type: "object",
        properties,
        required: Object.keys(properties).sort(),
        additionalProperties: false,
      };
    }
    return { const: structuredClone(value) };
  }

  function schemaChild(schemaValue, name) {
    const schema = normalizeSchema(schemaValue);
    const constraints = [];
    const hasObjectShape = ["type", "properties", "required", "additionalProperties"]
      .some((keyword) => Object.prototype.hasOwnProperty.call(schema, keyword));
    if (hasObjectShape) {
      if (!schemaTypes(schema).has("object")) return null;
      if (Object.prototype.hasOwnProperty.call(schema.properties || {}, name)) {
        constraints.push(schema.properties[name]);
      } else if (schema.additionalProperties === false) {
        return null;
      } else {
        constraints.push(
          schema.additionalProperties && typeof schema.additionalProperties === "object"
            ? schema.additionalProperties
            : {},
        );
      }
    }
    if (Object.prototype.hasOwnProperty.call(schema, "const")) {
      const value = schema.const;
      if (!value || typeof value !== "object" || Array.isArray(value)
          || !Object.prototype.hasOwnProperty.call(value, name)) return null;
      constraints.push(literalSchema(value[name]));
    }
    if (Array.isArray(schema.enum)) {
      const children = schema.enum
        .filter((value) => value && typeof value === "object" && !Array.isArray(value)
          && Object.prototype.hasOwnProperty.call(value, name))
        .map((value) => literalSchema(value[name]));
      if (!children.length) return null;
      constraints.push(children.length === 1 ? children[0] : { anyOf: children });
    }
    for (const keyword of ["anyOf", "oneOf"]) {
      if (!Array.isArray(schema[keyword])) continue;
      const children = schema[keyword]
        .map((branch) => schemaChild(branch, name))
        .filter((child) => child !== null);
      if (!children.length) return null;
      constraints.push(children.length === 1 ? children[0] : { anyOf: children });
    }
    if (Array.isArray(schema.allOf)) {
      const children = schema.allOf.map((branch) => schemaChild(branch, name));
      if (children.some((child) => child === null)) return null;
      constraints.push(children.length === 1 ? children[0] : { allOf: children });
    }
    if (!constraints.length) return {};
    return normalizeSchema(
      constraints.length === 1 ? constraints[0] : { allOf: constraints },
    );
  }

  function resolveDataKeyDeclaration(result, spec, pathValue) {
    const path = String(pathValue || "").trim();
    const parts = path.split(".");
    if (!path || parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) return null;
    const declarations = dataKeyDeclarations(result, spec);
    if (declarations[path]) return structuredClone(declarations[path]);
    const root = declarations[parts[0]];
    if (!root) return null;
    let schema = normalizeSchema(root.schema);
    for (const segment of parts.slice(1)) {
      schema = schemaChild(schema, segment);
      if (schema === null) return null;
    }
    const suffix = parts.slice(1).join(".");
    return {
      ...structuredClone(root),
      label: path,
      schema,
      source: root.source
        ? { ...root.source, path: `${root.source.path}.${suffix}` }
        : { path: `cycles.data.${path}` },
      encoding: {
        ...(root.encoding || {}),
        value: `${root.encoding?.value || `data.${parts[0]}`}.${suffix}`,
      },
    };
  }

  function layerFromDataKey(key, declaration) {
    return {
      id: key,
      dataKey: key,
      label: declaration.label || key,
      renderer: "series.line",
      source: declaration.source,
      encoding: declaration.encoding || {},
      paneRole: declaration.paneRole || "line",
      dataSchema: normalizeSchema(declaration.schema),
    };
  }

  function chartLayerCatalog(result, spec = {}) {
    return Object.entries(dataKeyDeclarations(result, spec)).map(([key, declaration]) => layerFromDataKey(key, declaration));
  }

  const jsonTypes = new Set(["null", "boolean", "object", "array", "number", "integer", "string"]);
  const schemaKeys = new Set(["type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf", "oneOf", "allOf", "title", "description", "default"]);

  function normalizeSchema(value) {
    if (value === true) return {};
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Data contracts must be JSON Schema objects; string aliases are forbidden");
    }
    const schema = structuredClone(value);
    const unknown = Object.keys(schema).filter((key) => !schemaKeys.has(key));
    if (unknown.length) throw new Error(`Unsupported JSON Schema keyword(s): ${unknown.sort().join(", ")}`);
    if (schema.type !== undefined) {
      const types = Array.isArray(schema.type) ? schema.type : [schema.type];
      if (!types.length || types.some((type) => !jsonTypes.has(type))) throw new Error("Invalid JSON Schema type");
    }
    if (schema.properties !== undefined) {
      if (!schema.properties || typeof schema.properties !== "object" || Array.isArray(schema.properties)) throw new Error("Schema properties must be an object");
      schema.properties = Object.fromEntries(Object.entries(schema.properties).map(([name, child]) => [name, normalizeSchema(child)]));
    }
    if (schema.items !== undefined) schema.items = normalizeSchema(schema.items);
    if (schema.additionalProperties && typeof schema.additionalProperties === "object") schema.additionalProperties = normalizeSchema(schema.additionalProperties);
    for (const keyword of ["anyOf", "oneOf", "allOf"]) {
      if (schema[keyword] !== undefined) {
        if (!Array.isArray(schema[keyword]) || !schema[keyword].length) throw new Error(`Schema ${keyword} must be a non-empty array`);
        schema[keyword] = schema[keyword].map(normalizeSchema);
      }
    }
    return schema;
  }

  function schemaTypes(value) {
    const schema = normalizeSchema(value);
    if (schema.type !== undefined) return new Set(Array.isArray(schema.type) ? schema.type : [schema.type]);
    if (schema.properties || schema.required) return new Set(["object"]);
    if (schema.items) return new Set(["array"]);
    return new Set(jsonTypes);
  }

  function schemasCompatible(provided, required) {
    const source = normalizeSchema(provided);
    const target = normalizeSchema(required);
    if (!Object.keys(target).length) return true;
    if (!Object.keys(source).length) return false;

    if (Object.prototype.hasOwnProperty.call(target, "const")) {
      if (Object.prototype.hasOwnProperty.call(source, "const")) return source.const === target.const;
      if (Array.isArray(source.enum)) return source.enum.every((item) => item === target.const);
      return false;
    }
    if (Array.isArray(target.enum)) {
      if (Object.prototype.hasOwnProperty.call(source, "const")) return target.enum.includes(source.const);
      if (Array.isArray(source.enum)) return source.enum.every((item) => target.enum.includes(item));
      return false;
    }

    if (Array.isArray(source.allOf)) return source.allOf.every((branch) => schemasCompatible(branch, target));
    if (Array.isArray(source.anyOf) || Array.isArray(source.oneOf)) {
      return (source.anyOf || source.oneOf).every((branch) => schemasCompatible(branch, target));
    }
    if (Array.isArray(target.allOf)) return target.allOf.every((branch) => schemasCompatible(source, branch));
    if (Array.isArray(target.anyOf) || Array.isArray(target.oneOf)) {
      return (target.anyOf || target.oneOf).some((branch) => schemasCompatible(source, branch));
    }

    const sourceTypes = schemaTypes(source);
    const targetTypes = schemaTypes(target);
    for (const sourceType of sourceTypes) {
      if (sourceType === "integer" && targetTypes.has("number")) continue;
      if (!targetTypes.has(sourceType)) return false;
    }

    if (sourceTypes.has("object") && targetTypes.has("object")) {
      const sourceProperties = source.properties || {};
      const targetProperties = target.properties || {};
      const sourceRequired = new Set(source.required || []);
      const sourceExtra = source.additionalProperties === undefined ? true : source.additionalProperties;
      const targetExtra = target.additionalProperties === undefined ? true : target.additionalProperties;
      for (const name of target.required || []) {
        if (!sourceRequired.has(name)) return false;
        const sourceProperty = Object.prototype.hasOwnProperty.call(sourceProperties, name)
          ? sourceProperties[name] : sourceExtra;
        const targetProperty = Object.prototype.hasOwnProperty.call(targetProperties, name)
          ? targetProperties[name] : targetExtra;
        if (sourceProperty === false || targetProperty === false) return false;
        if (!schemasCompatible(sourceProperty === true ? {} : sourceProperty, targetProperty === true ? {} : targetProperty)) return false;
      }
      for (const [name, sourceProperty] of Object.entries(sourceProperties)) {
        const targetProperty = Object.prototype.hasOwnProperty.call(targetProperties, name)
          ? targetProperties[name] : targetExtra;
        if (targetProperty === false) return false;
        if (targetProperty !== true && !schemasCompatible(sourceProperty, targetProperty)) return false;
      }
      if (sourceExtra !== false) {
        for (const [name, targetProperty] of Object.entries(targetProperties)) {
          if (Object.prototype.hasOwnProperty.call(sourceProperties, name)) continue;
          if (sourceExtra === true || !schemasCompatible(sourceExtra, targetProperty)) return false;
        }
        if (targetExtra === false) return false;
        if (targetExtra !== true && (sourceExtra === true || !schemasCompatible(sourceExtra, targetExtra))) return false;
      }
    }
    if (sourceTypes.has("array") && targetTypes.has("array") && target.items) {
      if (!source.items || !schemasCompatible(source.items, target.items)) return false;
    }
    return true;
  }

  function mergeObjectSchemas(left, right, path) {
    const first = normalizeSchema(left);
    const second = normalizeSchema(right);
    if (!schemaTypes(first).has("object") || !schemaTypes(second).has("object")) throw new Error(`DataKey parent '${path}' is not an object`);
    const result = structuredClone(first);
    result.properties ||= {};
    for (const [name, child] of Object.entries(second.properties || {})) {
      if (!result.properties[name]) result.properties[name] = child;
      else if (JSON.stringify(result.properties[name]) !== JSON.stringify(child)) {
        if (schemaTypes(result.properties[name]).has("object") && schemaTypes(child).has("object")) {
          result.properties[name] = mergeObjectSchemas(result.properties[name], child, `${path}.${name}`);
        } else throw new Error(`DataKey '${path}.${name}' has conflicting schemas`);
      }
    }
    result.required = [...new Set([...(result.required || []), ...(second.required || [])])].sort();
    return normalizeSchema(result);
  }

  function expandSchemaPaths(contracts) {
    const expanded = Object.fromEntries(Object.entries(contracts || {}).map(([path, schema]) => [path, normalizeSchema(schema)]));
    function addDeclaredProperties(path, schema) {
      for (const [name, child] of Object.entries(schema.properties || {})) {
        const childPath = `${path}.${name}`;
        expanded[childPath] ||= child;
        addDeclaredProperties(childPath, child);
      }
    }
    Object.entries({ ...expanded }).forEach(([path, schema]) => addDeclaredProperties(path, schema));
    Object.keys(expanded).sort((a, b) => b.split(".").length - a.split(".").length).forEach((path) => {
      const parts = path.split(".");
      if (parts.length < 2) return;
      const parent = parts.slice(0, -1).join(".");
      const name = parts[parts.length - 1];
      const fragment = { type: "object", properties: { [name]: expanded[path] }, required: [name] };
      expanded[parent] = expanded[parent] ? mergeObjectSchemas(expanded[parent], fragment, parent) : fragment;
    });
    return expanded;
  }

  function dataKeyOptions(result, spec, predicate = null) {
    return chartLayerCatalog(result, spec)
      .filter((item) => !predicate || predicate(item))
      .map((item) => ({ value: item.dataKey, label: item.label || item.dataKey, schema: item.dataSchema, paneRole: item.paneRole }))
      .sort((a, b) => String(a.label).localeCompare(String(b.label)));
  }

  let visualizerDefinitions = [];
  let temporaryModuleDefinitions = [];

  function setVisualizerDefinitions(definitions) {
    visualizerDefinitions = Array.isArray(definitions) ? structuredClone(definitions) : [];
  }

  function setTemporaryModuleDefinitions(definitions) {
    temporaryModuleDefinitions = Array.isArray(definitions)
      ? structuredClone(definitions)
      : Object.values(definitions || {}).map((definition) => structuredClone(definition));
  }

  function temporaryModuleDefinition(module) {
    return temporaryModuleDefinitions.find((definition) => (
      definition.kind === module?.kind
      && definition.moduleId === module?.moduleId
      && String(definition.version) === String(module?.version)
    ));
  }

  function visualizerCatalog(result, spec) {
    return visualizerDefinitions.map((definition) => ({
      ...structuredClone(definition),
      optionMap: Object.fromEntries(
        Object.entries(definition.inputPorts || {}).map(([name, port]) => [
          name,
          dataKeyOptions(result, spec, (item) => schemasCompatible(item.dataSchema, port.schema || {})),
        ]),
      ),
    }));
  }

  function layerFromVisualizerInstance(result, spec, instance) {
    const callback = instance?.callback;
    const params = { ...(instance?.params || {}) };
    if (callback === "ohlc.candles") {
      const dataKey = params.dataKey;
      const declaration = resolveDataKeyDeclaration(result, spec, dataKey);
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      const valuePath = declaration.encoding?.value || `data.${dataKey}`;
      return {
        ...base,
        id: instance.id || dataKey,
        dataKey,
        renderer: callback,
        params,
        source: declaration.source || { path: declaration.path },
        encoding: {
          time: declaration.encoding?.time || `${valuePath}.time`,
          open: `${valuePath}.open`, high: `${valuePath}.high`,
          low: `${valuePath}.low`, close: `${valuePath}.close`,
          complete: `${valuePath}.complete`,
        },
        visible: instance.visible !== false,
      };
    }
    if (callback === "series.line" || callback === "series.scatter" || callback === "series.histogram" || callback === "overlay.priceLine") {
      const dataKey = params.dataKey;
      const declaration = resolveDataKeyDeclaration(result, spec, dataKey);
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      return { ...base, id: instance.id || dataKey, dataKey, renderer: callback, params, visible: instance.visible !== false };
    }
    if (callback === "overlay.markers") {
      const dataKey = params.dataKey;
      const targetDataKey = params.targetDataKey;
      const declaration = resolveDataKeyDeclaration(result, spec, dataKey);
      if (!declaration) return null;
      const base = layerFromDataKey(dataKey, declaration);
      const valuePath = declaration.encoding?.value || `data.${dataKey}`;
      return {
        ...base,
        id: instance.id || dataKey,
        dataKey,
        renderer: callback,
        target: targetDataKey,
        encoding: {
          time: declaration.encoding?.time || "decisionTime",
          event: valuePath,
          side: `${valuePath}.side`, text: `${valuePath}.reason`,
          shape: `${valuePath}.shape`, position: `${valuePath}.position`, color: `${valuePath}.color`,
        },
        params,
        visible: instance.visible !== false,
      };
    }
    return null;
  }

  function collectPaneSourcePaths(result, pane, spec = {}) {
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
        const declaration = resolveDataKeyDeclaration(result, spec, dataKey);
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
    const { timeZone = "UTC", showTime = false, logScale = false, ...chartOptions } = options;
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
        mode: priceScaleMode(logScale),
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "#d9e0e4",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time) => formatChartClock(time, { timeZone, showTime }),
      },
      crosshair: {
        mode: window.LightweightCharts.CrosshairMode?.Normal ?? 0,
        vertLine: { color: "#63717a", labelBackgroundColor: "#0f766e" },
        horzLine: { color: "#63717a", labelBackgroundColor: "#0f766e" },
      },
      localization: {
        priceFormatter: (price) => Number(price).toLocaleString(undefined, { maximumFractionDigits: 4 }),
        timeFormatter: (time) => formatChartClock(time, { timeZone, showTime }),
      },
      ...chartOptions,
    });
  }

  function priceScaleMode(logScale) {
    const modes = window.LightweightCharts.PriceScaleMode || {};
    return logScale ? (modes.Logarithmic ?? 1) : (modes.Normal ?? 0);
  }

  function rendererRows(result, layer) {
    const source = layer.source || {};
    if (Array.isArray(source.data)) return source.data;
    const rows = resolvePath(result, source.path);
    return Array.isArray(rows) ? rows : [];
  }

  function paneTimeInfo(result, pane, spec = {}) {
    const layers = (pane?.visualizers || [])
      .map((instance) => layerFromVisualizerInstance(result, spec, instance))
      .filter((layer) => layer && layer.visible !== false);
    const values = [];
    let explicitIntraday = false;
    layers.forEach((layer) => {
      const timeField = layer.encoding?.time || "date";
      rendererRows(result, layer).forEach((row) => {
        const raw = fieldValue(row, timeField);
        if (typeof raw === "string") {
          const clock = raw.match(/[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
          if (clock && (Number(clock[1]) || Number(clock[2]) || Number(clock[3] || 0))) explicitIntraday = true;
        }
        const value = chartTime(raw);
        if (typeof value === "number" && Number.isFinite(value)) values.push(value);
      });
    });
    const sorted = [...new Set(values)].sort((a, b) => a - b);
    const subDailySpacing = sorted.some((value, index) => index > 0 && value - sorted[index - 1] > 0 && value - sorted[index - 1] < 86400);
    return {
      start: sorted.length ? sorted[0] : null,
      end: sorted.length ? sorted[sorted.length - 1] : null,
      showTime: explicitIntraday || subDailySpacing,
    };
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
      series.setData(completeCandlePoints(rendererRows(ctx.result, layer), enc));
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
        lineWidth: style.lineWidth || 3,
        lineStyle: lineStyle(ctx.library, style.lineStyle),
        priceLineVisible: style.priceLineVisible ?? false,
        lastValueVisible: style.lastValueVisible ?? true,
      });
      // Missing observations stay missing in the Result.  Removing them only
      // for rendering makes Lightweight Charts connect each pair of adjacent
      // valid observations without zero-fill or forward-fill semantics.
      series.setData(sparseLinePoints(rendererRows(ctx.result, layer), enc));
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
      // Scatter uses the same sparse-value contract as a line.  This is also
      // how an open-only current day/week can be shown without inventing HLC.
      series.setData(sparseLinePoints(rendererRows(ctx.result, layer), enc));
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
      const markers = rendererRows(ctx.result, layer)
        .filter((row) => !enc.event || fieldValue(row, enc.event) !== null && fieldValue(row, enc.event) !== undefined)
        .map((row) => {
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
    const ctx = { library, chart, result, pane, spec, seriesByLayerId: new Map(), seriesByDataKey: new Map(), primarySeries: null, cleanups: [] };
    layers.filter((layer) => !renderers[layer.renderer]?.overlay).forEach((layer) => renderers[layer.renderer]?.draw(ctx, layer));
    layers.filter((layer) => renderers[layer.renderer]?.overlay).forEach((layer) => renderers[layer.renderer]?.draw(ctx, layer));
    return ctx;
  }

  window.TradeChartCore = {
    chartTime,
    chartLayerCatalog,
    createFinancialChart,
    dataKeyDeclarations,
    resolveDataKeyDeclaration,
    expandSchemaPaths,
    normalizeSchema,
    schemasCompatible,
    drawCallbackCatalog,
    drawFinancialPane,
    visualizerCatalog,
    setVisualizerDefinitions,
    setTemporaryModuleDefinitions,
    collectPaneSourcePaths,
    layerId,
    normalizeVisualizationSpec,
    paneTimeInfo,
    parseRangeInput,
    formatRangeInput,
    priceScaleMode,
    sparseLinePoints,
    completeCandlePoints,
    createTemporaryModuleInstance,
    createVisualizerInstance,
    registerDrawCallback,
    registerRenderer,
    upsertIdentity,
  };
}());
