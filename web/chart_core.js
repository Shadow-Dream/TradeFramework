(function () {
  function hasOwn(value, key) {
    return value !== null && value !== undefined
      && Object.prototype.hasOwnProperty.call(value, key);
  }

  function resolvePath(root, path) {
    if (!path) return root;
    return String(path).split(".").reduce((node, key) => (
      hasOwn(node, key) ? node[key] : undefined
    ), root);
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
    if (!options.showTime) return `${parts.month}/${parts.day}/${parts.year}`;
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
    const sorted = rows
      .filter((row) => row.time !== undefined && row.time !== null)
      .sort((a, b) => (typeof a.time === "number" && typeof b.time === "number" ? a.time - b.time : String(a.time).localeCompare(String(b.time))));
    const unique = [];
    sorted.forEach((row) => {
      const previous = unique[unique.length - 1];
      if (previous && previous.time === row.time) unique[unique.length - 1] = row;
      else unique.push(row);
    });
    return unique;
  }

  function deepFreeze(value, seen = new WeakSet()) {
    if (!value || typeof value !== "object" || seen.has(value)) return value;
    seen.add(value);
    Object.values(value).forEach((child) => deepFreeze(child, seen));
    return Object.freeze(value);
  }

  function frozenClone(value) {
    return deepFreeze(structuredClone(value));
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function validChartTime(value) {
    if (finiteNumber(value)) return true;
    if (typeof value === "string" && value.trim()) return !Number.isNaN(Date.parse(value));
    return !!(value && typeof value === "object" && value.year && value.month && value.day);
  }

  function requireOffsetBars(value, label = "Series") {
    const offsetBars = value === undefined ? 0 : value;
    if (!Number.isSafeInteger(offsetBars)) {
      throw runtimeError("invalid-series-offset", `${label} offsetBars must be a safe integer.`);
    }
    return offsetBars;
  }

  function shiftedTimeIndex(index, offsetBars, timeCount) {
    if (offsetBars >= 0) {
      return offsetBars >= timeCount - index ? -1 : index + offsetBars;
    }
    return -offsetBars > index ? -1 : index + offsetBars;
  }

  function sparseLinePoints(values, times, requestedOffsetBars = 0) {
    const offsetBars = requireOffsetBars(requestedOffsetBars);
    const timeCount = Array.isArray(times) ? times.length : 0;
    return sortByTime((values || []).flatMap((value, index) => {
      const targetIndex = shiftedTimeIndex(index, offsetBars, timeCount);
      if (targetIndex < 0) return [];
      const time = chartTime(times[targetIndex]);
      return finiteNumber(value) && validChartTime(time) ? [{ time, value }] : [];
    }));
  }

  function completeCandlePoints(values) {
    return sortByTime((values || []).flatMap((value) => {
      if (!value || typeof value !== "object" || value.complete === false) return [];
      const candle = {
        time: chartTime(value.eventTime),
        open: value.open,
        high: value.high,
        low: value.low,
        close: value.close,
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

  function dataKeyCatalogDiagnostic(error, context = {}) {
    return {
      code: error?.code || "data-key-catalog-error",
      message: error?.message || "DataKey is unavailable.",
      unavailable: true,
      source: context.source || "result",
      dataKey: context.dataKey || "",
      portName: context.portName || "",
      module: context.module ? { ...context.module } : null,
    };
  }

  function buildBaseDataKeyCatalog(result, spec = {}) {
    const diagnostics = [];
    const candidates = Object.create(null);
    let resultKeys = [];
    try { resultKeys = Object.keys(result?.dataKeys || {}); } catch (error) {
      diagnostics.push(dataKeyCatalogDiagnostic(error));
    }
    for (const dataKey of resultKeys) {
      try {
        const declaration = result.dataKeys[dataKey];
        if (!declaration || typeof declaration !== "object" || Array.isArray(declaration)
            || !Object.prototype.hasOwnProperty.call(declaration, "schema")) {
          throw runtimeError("missing-data-key-schema", `Result DataKey '${dataKey}' has no schema contract.`);
        }
        candidates[dataKey] = { ...structuredClone(declaration), schema: normalizeSchema(declaration.schema) };
      } catch (error) {
        diagnostics.push(dataKeyCatalogDiagnostic(error, { dataKey }));
      }
    }
    for (const module of spec?.temporaryModules || []) {
      let identity;
      let outputs;
      try {
        identity = {
          instanceId: module?.instanceId,
          kind: module?.kind,
          moduleId: module?.moduleId,
          version: module?.version,
        };
        outputs = module?.outputs || {};
      } catch (error) {
        diagnostics.push(dataKeyCatalogDiagnostic(error, { source: "temporary-module" }));
        continue;
      }
      const definition = temporaryModuleDefinition(identity);
      let portNames = [];
      try { portNames = Object.keys(outputs); } catch (error) {
        diagnostics.push(dataKeyCatalogDiagnostic(error, {
          source: "temporary-module", module: identity,
        }));
        continue;
      }
      for (const portName of portNames) {
        let dataKey = "";
        try {
          dataKey = outputs[portName];
          if (typeof dataKey !== "string" || !dataKey) continue;
          if (!definition) {
            throw runtimeError(
              "unknown-temporary-module",
              `Temporary Module '${identity.instanceId || "unknown"}' has no installed Definition.`,
            );
          }
          const port = definition?.ports?.outputs?.[portName];
          if (!port || !Object.prototype.hasOwnProperty.call(port, "schema")) {
            throw runtimeError(
              "unknown-temporary-output",
              `Temporary Module '${identity.instanceId || "unknown"}' output '${portName}' has no schema contract.`,
            );
          }
          let schema;
          try {
            schema = normalizeSchema(port.schema);
          } catch (error) {
            throw runtimeError(
              "temporary-schema-invalid",
              `Temporary Module '${identity.instanceId || "unknown"}' output '${portName}' schema is invalid: ${error?.message || "invalid schema"}`,
            );
          }
          const parts = dataKey.split(".");
          if (parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) {
            throw runtimeError("invalid-data-key", `Temporary output DataKey '${dataKey}' is invalid.`);
          }
          for (let index = 1; index < parts.length; index += 1) {
            const parent = parts.slice(0, index).join(".");
            if (!hasOwn(candidates, parent)) {
              candidates[parent] = {
                label: parent,
                schema: { type: "object" },
                source: { path: `cycles.data.${parent}` },
                encoding: { value: `data.${parent}` },
              };
            }
          }
          candidates[dataKey] = {
            label: dataKey,
            schema,
            source: { path: `cycles.data.${dataKey}` },
            encoding: { value: `data.${dataKey}` },
            paneRole: "line",
            module: {
              source: "temporary",
              ...identity,
              output: portName,
            },
          };
        } catch (error) {
          diagnostics.push(dataKeyCatalogDiagnostic(error, {
            source: "temporary-module-output",
            dataKey: typeof dataKey === "string" ? dataKey : "",
            portName,
            module: identity,
          }));
        }
      }
    }
    const expanded = Object.assign(Object.create(null), candidates);
    function addProperties(path, declaration) {
      const schema = declaration.schema;
      for (const [name, childSchema] of Object.entries(schema.properties || {})) {
        const childPath = `${path}.${name}`;
        if (!hasOwn(expanded, childPath)) {
          expanded[childPath] = {
            ...declaration,
            label: childPath,
            schema: childSchema,
            source: declaration.source?.path
              ? { ...declaration.source, path: `${declaration.source.path}.${name}` }
              : declaration.source,
            encoding: {
              ...(declaration.encoding || {}),
              value: `${declaration.encoding?.value || `data.${path}`}.${name}`,
            },
          };
        }
        addProperties(childPath, expanded[childPath]);
      }
    }
    Object.entries(expanded).forEach(([path, declaration]) => addProperties(path, declaration));
    return { declarations: expanded, diagnostics };
  }

  function baseDataKeyDeclarations(result, spec = {}) {
    return buildBaseDataKeyCatalog(result, spec).declarations;
  }

  function dataKeyCatalogDiagnostics(result, spec = {}) {
    return structuredClone(buildBaseDataKeyCatalog(result, spec).diagnostics);
  }

  function concreteDataKeyPaths(result) {
    const paths = new Set();
    function visit(value, parent = []) {
      if (!value || typeof value !== "object" || Array.isArray(value)) return;
      for (const [name, child] of Object.entries(value)) {
        if (!/^[A-Za-z0-9_-]+$/.test(name)) continue;
        const parts = [...parent, name];
        paths.add(parts.join("."));
        visit(child, parts);
      }
    }
    for (const cycle of result?.cycles || []) visit(cycle?.data);
    return [...paths].sort((left, right) => (
      left.split(".").length - right.split(".").length || left.localeCompare(right)
    ));
  }

  function dataKeyDeclarations(result, spec = {}) {
    const expanded = baseDataKeyDeclarations(result, spec);
    for (const path of concreteDataKeyPaths(result)) {
      if (hasOwn(expanded, path)) continue;
      const declaration = resolveDataKeyDeclarationFrom(expanded, path);
      if (declaration) expanded[path] = declaration;
    }
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
    if (schema === false) return null;
    const constraints = [];
    const hasObjectShape = ["type", "properties", "required", "additionalProperties"]
      .some((keyword) => Object.prototype.hasOwnProperty.call(schema, keyword));
    if (hasObjectShape) {
      if (!schemaTypes(schema).has("object")) return null;
      if (hasOwn(schema.properties || {}, name)) {
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
          || !hasOwn(value, name)) return null;
      constraints.push(literalSchema(value[name]));
    }
    if (Array.isArray(schema.enum)) {
      const children = schema.enum
        .filter((value) => value && typeof value === "object" && !Array.isArray(value)
          && hasOwn(value, name))
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

  function resolveDataKeyDeclarationFrom(declarations, pathValue) {
    const path = String(pathValue || "").trim();
    const parts = path.split(".");
    if (!path || parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) return null;
    if (hasOwn(declarations, path)) return structuredClone(declarations[path]);
    if (!hasOwn(declarations, parts[0])) return null;
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

  function resolveDataKeyDeclaration(result, spec, pathValue) {
    return resolveDataKeyDeclarationFrom(
      baseDataKeyDeclarations(result, spec),
      pathValue,
    );
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
  const schemaAnnotationKeys = new Set(["title", "description", "default"]);
  const schemaKeys = new Set(["type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf", "oneOf", "allOf", "title", "description", "default"]);

  function normalizeSchema(value) {
    if (value === true) return {};
    if (value === false) return false;
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
    if (schema === false) return new Set();
    if (schema.type !== undefined) return new Set(Array.isArray(schema.type) ? schema.type : [schema.type]);
    if (schema.properties || schema.required) return new Set(["object"]);
    if (schema.items) return new Set(["array"]);
    return new Set(jsonTypes);
  }

  function jsonValuesEqual(left, right) {
    if (left === null || right === null) return left === right;
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) && Array.isArray(right)
        && left.length === right.length
        && left.every((value, index) => jsonValuesEqual(value, right[index]));
    }
    if (typeof left === "object" || typeof right === "object") {
      if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
      const leftKeys = Object.keys(left);
      const rightKeys = Object.keys(right);
      return leftKeys.length === rightKeys.length
        && leftKeys.every((key) => Object.prototype.hasOwnProperty.call(right, key)
          && jsonValuesEqual(left[key], right[key]));
    }
    return typeof left === typeof right && Object.is(left, right);
  }

  function jsonType(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return "non-json";
      return Number.isInteger(value) ? "integer" : "number";
    }
    if (["boolean", "string"].includes(typeof value)) return typeof value;
    if (value && typeof value === "object") return "object";
    return "non-json";
  }

  function schemaAcceptsValue(schemaValue, value) {
    const schema = normalizeSchema(schemaValue);
    if (schema === false) return false;
    const actual = jsonType(value);
    if (actual === "non-json") return false;
    if ((schema.allOf || []).some((branch) => !schemaAcceptsValue(branch, value))) return false;
    if (schema.anyOf && !schema.anyOf.some((branch) => schemaAcceptsValue(branch, value))) return false;
    if (schema.oneOf && schema.oneOf.filter((branch) => schemaAcceptsValue(branch, value)).length !== 1) return false;
    const allowed = schemaTypes(schema);
    if (!allowed.has(actual) && !(actual === "integer" && allowed.has("number"))) return false;
    if (Object.prototype.hasOwnProperty.call(schema, "const")
        && !jsonValuesEqual(value, schema.const)) return false;
    if (schema.enum && !schema.enum.some((candidate) => jsonValuesEqual(value, candidate))) return false;
    if (actual === "object") {
      const properties = schema.properties || {};
      if ((schema.required || []).some((name) => !Object.prototype.hasOwnProperty.call(value, name))) return false;
      const additional = schema.additionalProperties === undefined ? true : schema.additionalProperties;
      for (const [name, child] of Object.entries(value)) {
        const childSchema = Object.prototype.hasOwnProperty.call(properties, name)
          ? properties[name] : additional;
        if (childSchema === false || !schemaAcceptsValue(childSchema === true ? {} : childSchema, child)) return false;
      }
    } else if (actual === "array") {
      const itemSchema = schema.items === undefined ? {} : schema.items;
      if (value.some((child) => !schemaAcceptsValue(itemSchema, child))) return false;
    }
    return true;
  }

  function schemaBase(schema) {
    return Object.fromEntries(Object.entries(schema).filter(([keyword]) => (
      !["allOf", "anyOf", "oneOf"].includes(keyword)
    )));
  }

  function sourceContext(schema) {
    const atoms = [];
    const unions = [];
    function add(item) {
      if (item === false) {
        atoms.push(false);
        return;
      }
      const base = schemaBase(item);
      if (Array.isArray(base.type)) {
        const branchBase = { ...base };
        delete branchBase.type;
        unions.push({
          keyword: "anyOf",
          branches: base.type.map((type) => ({ ...branchBase, type })),
        });
        atoms.push({});
      } else {
        atoms.push(base);
      }
      for (const branch of item.allOf || []) add(branch);
      for (const keyword of ["anyOf", "oneOf"]) {
        if (item[keyword]) unions.push({ keyword, branches: item[keyword] });
      }
    }
    add(schema);
    return { atoms, unions };
  }

  function targetContext(schema) {
    const atoms = [];
    const unions = [];
    function add(item) {
      if (item === false) {
        atoms.push(false);
        return;
      }
      atoms.push(schemaBase(item));
      for (const branch of item.allOf || []) add(branch);
      for (const keyword of ["anyOf", "oneOf"]) {
        if (item[keyword]) unions.push({ keyword, branches: item[keyword] });
      }
    }
    add(schema);
    return { atoms, unions };
  }

  function sourceContextLiterals(context) {
    let candidates = null;
    for (const schema of context.atoms) {
      if (schema === false) return [];
      if (Object.prototype.hasOwnProperty.call(schema, "const")) {
        candidates = [schema.const];
        break;
      }
      if (schema.enum) {
        candidates = schema.enum;
        break;
      }
    }
    if (candidates === null) return null;
    return candidates.filter((candidate) => (
      context.atoms.every((schema) => schemaAcceptsValue(schema, candidate))
      && context.unions.every(({ keyword, branches }) => (
        schemaAcceptsValue({ [keyword]: branches }, candidate)
      ))
    ));
  }

  function schemasDisjointNormalized(left, right) {
    if (left === false || right === false) return true;
    if (!Object.keys(left).length || !Object.keys(right).length) return false;
    if (Object.prototype.hasOwnProperty.call(left, "const")) return !schemaAcceptsValue(right, left.const);
    if (Object.prototype.hasOwnProperty.call(right, "const")) return !schemaAcceptsValue(left, right.const);
    if (left.enum) return left.enum.every((value) => !schemaAcceptsValue(right, value));
    if (right.enum) return right.enum.every((value) => !schemaAcceptsValue(left, value));
    if (left.anyOf || left.oneOf) {
      return (left.anyOf || left.oneOf).every((branch) => schemasDisjointNormalized(branch, right));
    }
    if (right.anyOf || right.oneOf) {
      return (right.anyOf || right.oneOf).every((branch) => schemasDisjointNormalized(left, branch));
    }
    if (left.allOf?.some((branch) => schemasDisjointNormalized(branch, right))) return true;
    if (right.allOf?.some((branch) => schemasDisjointNormalized(left, branch))) return true;
    const leftTypes = schemaTypes(left);
    const rightTypes = schemaTypes(right);
    const overlaps = (leftType, rightType) => (
      leftType === rightType || new Set([leftType, rightType]).size === 2
        && [leftType, rightType].every((type) => ["integer", "number"].includes(type))
    );
    const overlappingPairs = [...leftTypes].flatMap((leftType) => (
      [...rightTypes].filter((rightType) => overlaps(leftType, rightType)).map((rightType) => [leftType, rightType])
    ));
    if (!overlappingPairs.length) return true;
    if (overlappingPairs.every(([leftType, rightType]) => leftType === "object" && rightType === "object")) {
      const leftProperties = left.properties || {};
      const rightProperties = right.properties || {};
      const leftRequired = new Set(left.required || []);
      const rightRequired = new Set(right.required || []);
      for (const name of [...leftRequired].filter((item) => rightRequired.has(item))) {
        const leftProperty = Object.prototype.hasOwnProperty.call(leftProperties, name)
          ? leftProperties[name] : (left.additionalProperties ?? true);
        const rightProperty = Object.prototype.hasOwnProperty.call(rightProperties, name)
          ? rightProperties[name] : (right.additionalProperties ?? true);
        if (leftProperty === false || rightProperty === false) return true;
        if (leftProperty !== true && rightProperty !== true
            && schemasDisjointNormalized(leftProperty, rightProperty)) return true;
      }
      for (const name of [...leftRequired].filter((item) => !Object.prototype.hasOwnProperty.call(rightProperties, item))) {
        if ((right.additionalProperties ?? true) === false) return true;
      }
      for (const name of [...rightRequired].filter((item) => !Object.prototype.hasOwnProperty.call(leftProperties, item))) {
        if ((left.additionalProperties ?? true) === false) return true;
      }
    }
    return false;
  }

  function sourceContextIsEmpty(context) {
    if (context.atoms.some((schema) => schema === false)) return true;
    const literals = sourceContextLiterals(context);
    if (literals !== null) return !literals.length;
    return context.atoms.some((left, index) => (
      context.atoms.slice(index + 1).some((right) => schemasDisjointNormalized(left, right))
    ));
  }

  function contextWithBranch(atoms, branch) {
    const child = sourceContext(branch);
    return { atoms: [...atoms, ...child.atoms], unions: child.unions };
  }

  function sourceContextSubsetAtom(context, target) {
    if (sourceContextIsEmpty(context)) return true;
    const literals = sourceContextLiterals(context);
    if (literals !== null) return literals.every((value) => schemaAcceptsValue(target, value));
    if (context.atoms.some((source) => plainSchemaSubset(source, target))) return true;
    const targetConstraints = Object.keys(target).filter((keyword) => !schemaAnnotationKeys.has(keyword));
    if (targetConstraints.every((keyword) => keyword === "type")) {
      let possible = new Set(jsonTypes);
      for (const source of context.atoms) {
        const types = schemaTypes(source);
        if (types.has("number")) types.add("integer");
        possible = new Set([...possible].filter((type) => types.has(type)));
      }
      const targetTypes = schemaTypes(target);
      if (targetTypes.has("number")) targetTypes.add("integer");
      if ([...possible].every((type) => targetTypes.has(type))) return true;
    }
    return context.unions.some(({ branches }) => (
      branches.every((branch) => sourceContextSubsetAtom(contextWithBranch(context.atoms, branch), target))
    ));
  }

  function sourceContextDisjointSchema(context, target) {
    if (sourceContextIsEmpty(context)) return true;
    const literals = sourceContextLiterals(context);
    if (literals !== null) return literals.every((value) => !schemaAcceptsValue(target, value));
    if (context.atoms.some((source) => schemasDisjointNormalized(source, target))) return true;
    return context.unions.some(({ branches }) => (
      branches.every((branch) => sourceContextDisjointSchema(contextWithBranch(context.atoms, branch), target))
    ));
  }

  function sourceContextSubsetUnion(context, keyword, branches) {
    const literals = sourceContextLiterals(context);
    if (literals !== null) {
      return literals.every((literal) => schemaAcceptsValue({ [keyword]: branches }, literal));
    }
    if (keyword === "anyOf") {
      if (branches.some((branch) => sourceContextSubsetSchema(context, branch))) return true;
    } else {
      for (let index = 0; index < branches.length; index += 1) {
        if (sourceContextSubsetSchema(context, branches[index])
            && branches.every((other, otherIndex) => (
              otherIndex === index || sourceContextDisjointSchema(context, other)
            ))) return true;
      }
    }
    return context.unions.some(({ branches: sourceBranches }) => (
      sourceBranches.every((branch) => (
        sourceContextSubsetUnion(contextWithBranch(context.atoms, branch), keyword, branches)
      ))
    ));
  }

  function sourceContextSubsetSchema(context, target) {
    const targetParts = targetContext(target);
    return targetParts.atoms.every((targetAtom) => sourceContextSubsetAtom(context, targetAtom))
      && targetParts.unions.every(({ keyword, branches }) => (
        sourceContextSubsetUnion(context, keyword, branches)
      ));
  }

  function plainSchemaSubset(source, target) {
    if (jsonValuesEqual(source, target) || source === false) return true;
    if (target === false) return false;
    if (!Object.keys(target).length) return true;
    if (!Object.keys(source).length) return false;
    let sourceLiterals = null;
    if (Object.prototype.hasOwnProperty.call(source, "const")) sourceLiterals = [source.const];
    else if (source.enum) sourceLiterals = source.enum;
    if (sourceLiterals !== null) return sourceLiterals.every((value) => schemaAcceptsValue(target, value));
    if (Object.prototype.hasOwnProperty.call(target, "const") || target.enum) return false;
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
        if (sourceProperty === false || targetProperty === false
            || !schemasCompatibleNormalized(sourceProperty, targetProperty)) return false;
      }
      for (const [name, sourceProperty] of Object.entries(sourceProperties)) {
        const targetProperty = Object.prototype.hasOwnProperty.call(targetProperties, name)
          ? targetProperties[name] : targetExtra;
        if (targetProperty === false
            || targetProperty !== true && !schemasCompatibleNormalized(sourceProperty, targetProperty)) return false;
      }
      if (sourceExtra !== false) {
        for (const [name, targetProperty] of Object.entries(targetProperties)) {
          if (Object.prototype.hasOwnProperty.call(sourceProperties, name)) continue;
          if (sourceExtra === true || !schemasCompatibleNormalized(sourceExtra, targetProperty)) return false;
        }
        if (targetExtra === false
            || targetExtra !== true && (sourceExtra === true
              || !schemasCompatibleNormalized(sourceExtra, targetExtra))) return false;
      }
    }
    if (sourceTypes.has("array") && targetTypes.has("array") && target.items !== undefined) {
      if (source.items === undefined || !schemasCompatibleNormalized(source.items, target.items)) return false;
    }
    return true;
  }

  function schemasCompatibleNormalized(source, target) {
    if (jsonValuesEqual(source, target)) return true;
    if (source === false) return true;
    if (target === false) return source === false;
    if (!Object.keys(target).length) return true;
    if (!Object.keys(source).length) return false;
    return sourceContextSubsetSchema(sourceContext(source), target);
  }

  function schemasCompatible(provided, required) {
    return schemasCompatibleNormalized(normalizeSchema(provided), normalizeSchema(required));
  }

  function visualizerReadSchema(value) {
    const schema = normalizeSchema(value);
    if (schema === false) return false;
    for (const keyword of ["allOf", "anyOf", "oneOf"]) {
      if (Array.isArray(schema[keyword])) {
        schema[keyword] = schema[keyword].map(visualizerReadSchema);
      }
    }
    if (schema.properties) {
      schema.properties = Object.fromEntries(
        Object.entries(schema.properties).map(([name, child]) => [name, visualizerReadSchema(child)]),
      );
    }
    if (schemaTypes(schema).has("object")) schema.additionalProperties = true;
    return schema;
  }

  function visualizerSchemasCompatible(provided, required) {
    return schemasCompatible(
      normalizeSchema(provided),
      visualizerReadSchema(required),
    );
  }

  function mergeObjectSchemas(left, right, path) {
    const first = normalizeSchema(left);
    const second = normalizeSchema(right);
    if (!schemaTypes(first).has("object") || !schemaTypes(second).has("object")) throw new Error(`DataKey parent '${path}' is not an object`);
    const result = structuredClone(first);
    result.properties = Object.assign(Object.create(null), result.properties || {});
    for (const [name, child] of Object.entries(second.properties || {})) {
      if (!hasOwn(result.properties, name)) result.properties[name] = child;
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
    const expanded = Object.create(null);
    for (const [path, schema] of Object.entries(contracts || {})) {
      expanded[path] = normalizeSchema(schema);
    }
    function addDeclaredProperties(path, schema) {
      for (const [name, child] of Object.entries(schema.properties || {})) {
        const childPath = `${path}.${name}`;
        if (!hasOwn(expanded, childPath)) expanded[childPath] = child;
        addDeclaredProperties(childPath, child);
      }
    }
    Object.entries(expanded).forEach(([path, schema]) => addDeclaredProperties(path, schema));
    Object.keys(expanded).sort((a, b) => b.split(".").length - a.split(".").length).forEach((path) => {
      const parts = path.split(".");
      if (parts.length < 2) return;
      const parent = parts.slice(0, -1).join(".");
      const name = parts[parts.length - 1];
      const fragment = { type: "object", properties: { [name]: expanded[path] }, required: [name] };
      expanded[parent] = hasOwn(expanded, parent)
        ? mergeObjectSchemas(expanded[parent], fragment, parent)
        : fragment;
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

  // Kept as a compatibility surface for older UI generations. Style is
  // instance-owned and this function intentionally has no allocator behavior.
  function allocateDistinctVisualizerStyles(_definition, params) {
    return { params: structuredClone(params || {}), changed: false };
  }

  function normalizeVisualizerStyleCollisions(spec, _definitions = visualizerDefinitions) {
    return { spec: structuredClone(spec || {}), changed: false };
  }

  function temporaryModuleDefinition(module) {
    return temporaryModuleDefinitions.find((definition) => (
      definition.kind === module?.kind
      && definition.moduleId === module?.moduleId
      && String(definition.version) === String(module?.version)
    ));
  }

  function schemaDiscoveryBranches(schemaValue, staticParts = [], output = []) {
    const schema = normalizeSchema(schemaValue);
    for (const [name, child] of Object.entries(schema.properties || {})) {
      schemaDiscoveryBranches(child, [...staticParts, name], output);
    }
    for (const keyword of ["allOf", "anyOf", "oneOf"]) {
      for (const branch of schema[keyword] || []) {
        schemaDiscoveryBranches(branch, staticParts, output);
      }
    }
    // A typed additionalProperties schema is an observed-key map. Project its
    // static parent. Discovery is a property of the Result contract and must
    // not change when a visualizer package is installed or removed.
    if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
      output.push(staticParts);
    }
    return output;
  }

  function discoverySourcePaths(result, spec = {}) {
    const candidates = new Set();
    for (const [dataKey, declaration] of Object.entries(baseDataKeyDeclarations(result, spec))) {
      if (!declaration?.schema) continue;
      try {
        const sourcePath = declaration.source?.path || `cycles.data.${dataKey}`;
        // Stop at the first runtime map key. Projecting that static prefix is
        // enough to enumerate every concrete child without loading all cycles.data.
        for (const staticParts of schemaDiscoveryBranches(declaration.schema)) {
          candidates.add([sourcePath, ...staticParts].join("."));
        }
      } catch {
        // Catalog diagnostics already identify the unavailable declaration.
      }
    }
    const sorted = [...candidates].sort((left, right) => (
      left.split(".").length - right.split(".").length || left.localeCompare(right)
    ));
    return sorted.filter((path, index) => !sorted.slice(0, index).some((parent) => (
      path.startsWith(`${parent}.`)
    )));
  }

  function visualizerCatalog(result, spec) {
    return visualizerDefinitions.map((definition) => {
      const copy = structuredClone(definition);
      try {
        copy.optionMap = Object.fromEntries(
          Object.entries(definition.inputPorts || {}).map(([name, port]) => [
            name,
            dataKeyOptions(result, spec, (item) => visualizerSchemasCompatible(item.dataSchema, port.schema || {})),
          ]),
        );
      } catch (error) {
        copy.optionMap = Object.fromEntries(
          Object.keys(definition.inputPorts || {}).map((name) => [name, []]),
        );
        copy.unavailableReason = error?.message || "Visualizer input contract is invalid.";
      }
      return copy;
    });
  }

  function visualizerDefinitionById(identifier) {
    return visualizerDefinitions.find((definition) => definition?.id === identifier) || null;
  }

  function visualizerInputBindings(instance) {
    const bindings = [];
    const definition = visualizerDefinitionById(instance?.callback);
    if (!definition) {
      throw runtimeError("unknown-visualizer", `Visualizer definition '${instance?.callback || "unknown"}' is unavailable.`);
    }
    definitionCapabilities(definition);
    for (const [portName, port] of Object.entries(definition.inputPorts || {})) {
      const dataKey = instance?.params?.[portName];
      if (typeof dataKey === "string" && dataKey.trim()) {
        bindings.push({
          dataKey,
          schema: port && hasOwn(port, "schema") ? port.schema : {},
        });
      }
    }
    return bindings;
  }

  function schemaAuthorizesRelativePath(schemaValue, segments) {
    if (!segments.length) return true;
    if (schemaValue === null) return false;
    const [name, ...rest] = segments;
    const child = schemaChild(schemaValue, name);
    return child !== null && schemaAuthorizesRelativePath(child, rest);
  }

  function validateTemporaryModuleForPlanning(module) {
    if (typeof module?.instanceId !== "string" || !module.instanceId) {
      throw runtimeError("invalid-temporary-module", "Temporary Module identity is missing.");
    }
    const definition = temporaryModuleDefinition(module);
    if (!definition) {
      throw runtimeError(
        "unknown-temporary-module",
        `Temporary Module '${module.instanceId}' has no installed Definition.`,
      );
    }
    for (const [portName, dataKey] of Object.entries(module.outputs || {})) {
      if (typeof dataKey !== "string" || !dataKey) continue;
      const port = definition?.ports?.outputs?.[portName];
      if (!port || !Object.prototype.hasOwnProperty.call(port, "schema")) {
        throw runtimeError(
          "unknown-temporary-output",
          `Temporary Module '${module.instanceId}' output '${portName}' has no schema contract.`,
        );
      }
      try {
        normalizeSchema(port.schema);
      } catch (error) {
        throw runtimeError(
          "temporary-schema-invalid",
          `Temporary Module '${module.instanceId}' output '${portName}' schema is invalid: ${error?.message || "invalid schema"}`,
        );
      }
    }
  }

  function dependencyPlanForInputKeys(result, modules, spec, inputBindings) {
    const requestedBindings = [...inputBindings];
    const requestedKeys = new Set(requestedBindings.map((binding) => binding.dataKey));
    const queue = [...requestedBindings];
    const selectedModuleIds = new Set();
    const baseSpec = { ...spec, temporaryModules: [] };
    const baseDeclaration = (dataKey) => resolveDataKeyDeclaration(result, baseSpec, dataKey);
    let normalizedModules = null;
    let producersByOutput = null;
    const ensureProducerIndex = () => {
      if (producersByOutput) return;
      normalizedModules = [];
      producersByOutput = new Map();
      const seen = new Set();
      for (const module of modules) {
        let instanceId;
        try { instanceId = module?.instanceId; } catch { continue; }
        if (typeof instanceId !== "string" || !instanceId || seen.has(instanceId)) continue;
        seen.add(instanceId);
        normalizedModules.push(module);
        try {
          for (const dataKey of Object.values(module?.outputs || {})) {
            if (typeof dataKey !== "string" || !dataKey) continue;
            const producers = producersByOutput.get(dataKey) || [];
            producers.push(module);
            producersByOutput.set(dataKey, producers);
          }
        } catch {
          // A malformed, unselected module is not allowed to poison another
          // instance's producer lookup.
        }
      }
    };
    const producersFor = (dataKey, schema) => {
      ensureProducerIndex();
      const matches = [];
      for (const [outputKey, producers] of producersByOutput.entries()) {
        const outputIsAncestor = dataKey === outputKey || dataKey.startsWith(`${outputKey}.`);
        const outputIsAuthorizedDescendant = outputKey.startsWith(`${dataKey}.`)
          && schemaAuthorizesRelativePath(schema, outputKey.slice(dataKey.length + 1).split("."));
        if (!outputIsAncestor && !outputIsAuthorizedDescendant) continue;
        if (producers.length !== 1) {
          throw runtimeError(
            "ambiguous-temporary-producer",
            `DataKey '${outputKey}' has multiple Temporary Module producers.`,
          );
        }
        matches.push(producers[0]);
      }
      return matches;
    };
    while (queue.length) {
      const { dataKey, schema = null } = queue.shift();
      const producers = producersFor(dataKey, schema);
      if (!producers.length) {
        if (baseDeclaration(dataKey)) continue;
        continue;
      }
      for (const producer of producers) {
        if (selectedModuleIds.has(producer.instanceId)) continue;
        selectedModuleIds.add(producer.instanceId);
        const definition = temporaryModuleDefinition(producer);
        for (const [portName, inputKey] of Object.entries(producer.inputs || {})) {
          if (typeof inputKey !== "string" || !inputKey) continue;
          const port = definition?.ports?.inputs?.[portName];
          queue.push({
            dataKey: inputKey,
            schema: port && hasOwn(port, "schema") ? port.schema : null,
          });
        }
      }
    }
    const temporaryModules = (normalizedModules || []).filter((module) => (
      selectedModuleIds.has(module.instanceId)
    ));
    temporaryModules.forEach(validateTemporaryModuleForPlanning);
    const dependencySpec = { ...spec, temporaryModules };
    const paths = new Set();
    requestedKeys.forEach((dataKey) => {
      const declaration = resolveDataKeyDeclaration(result, dependencySpec, dataKey);
      if (!declaration) {
        throw runtimeError("unknown-input", `DataKey '${dataKey}' has no Result or Temporary Module declaration.`);
      }
      const sourcePath = declaration?.source?.path || declaration?.path;
      if (!sourcePath) {
        throw runtimeError("unknown-input-source", `DataKey '${dataKey}' has no source path.`);
      }
      paths.add(sourcePath);
    });
    return { paths: [...paths], temporaryModules };
  }

  function planningErrorValue(error) {
    return {
      code: error?.code || "dependency-planning-error",
      message: publicDiagnosticMessage(error?.code, "dependency-planning-error"),
    };
  }

  function duplicatePaneVisualizerIds(pane) {
    const counts = new Map();
    for (const instance of pane?.visualizers || []) {
      let id;
      try { id = instance?.id; } catch { continue; }
      if (typeof id !== "string" || !id || unsafeMapKeys.has(id)) continue;
      counts.set(id, (counts.get(id) || 0) + 1);
    }
    return new Set([...counts].filter(([, count]) => count > 1).map(([id]) => id));
  }

  function requireUniquePaneVisualizerInstance(instance, duplicateIds) {
    const id = requireRuntimeInstanceId(instance);
    if (duplicateIds.has(id)) {
      throw runtimeError(
        "duplicate-visualizer-id",
        `Pane contains duplicate Visualizer ID '${id}'.`,
      );
    }
    const definition = visualizerDefinitionById(instance?.callback);
    if (!definition) return id;
    for (const requirement of definitionCapabilities(definition).requires) {
      if (!instance?.params || !hasOwn(instance.params, requirement.bindingParam)) continue;
      const targetId = instance.params[requirement.bindingParam];
      if (duplicateIds.has(targetId)) {
        throw runtimeError(
          "duplicate-visualizer-id",
          `Visualizer '${id}' capability '${requirement.name}' targets duplicate Visualizer ID '${targetId}'.`,
        );
      }
    }
    return id;
  }

  function visualizerDependencyPlan(result, pane, spec = {}) {
    const modules = [...(spec?.temporaryModules || []), ...(pane?.temporaryModules || [])];
    const duplicateIds = duplicatePaneVisualizerIds(pane);
    return (pane?.visualizers || []).flatMap((instance) => {
      if (!instance || instance.visible === false) return [];
      let visualizerId = "";
      try { visualizerId = typeof instance.id === "string" ? instance.id : String(instance.id || ""); } catch { /* handled below */ }
      try {
        visualizerId = requireUniquePaneVisualizerInstance(instance, duplicateIds);
        const plan = dependencyPlanForInputKeys(
          result, modules, spec, visualizerInputBindings(instance),
        );
        return [{
          visualizerId,
          paths: [...plan.paths],
          temporaryModules: structuredClone(plan.temporaryModules),
        }];
      } catch (error) {
        return [{
          visualizerId,
          paths: [],
          temporaryModules: [],
          planningError: planningErrorValue(error),
        }];
      }
    });
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

  const chartContainers = new WeakMap();

  function createFinancialChart(container, options = {}) {
    const { timeZone = "UTC", showTime = false, logScale = false, ...chartOptions } = options;
    const chart = window.LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { color: "#ffffff" },
        textColor: "#172026",
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        attributionLogo: false,
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
      // Lightweight Charts 5.2 can bring a series forward while it is hovered.
      // This is useful when several primary series intentionally share a pane.
      hoveredSeriesOnTop: true,
      ...chartOptions,
    });
    chartContainers.set(chart, container);
    return chart;
  }

  function priceScaleMode(logScale) {
    const modes = window.LightweightCharts.PriceScaleMode || {};
    return logScale ? (modes.Logarithmic ?? 1) : (modes.Normal ?? 0);
  }

  function dataKeyRows(result, declaration) {
    const source = declaration?.source || {};
    if (Array.isArray(source.data)) return source.data;
    if (source.path === "cycles" || String(source.path || "").startsWith("cycles.")) {
      return Array.isArray(result?.cycles) ? result.cycles : [];
    }
    const rows = resolvePath(result, source.path);
    return Array.isArray(rows) ? rows : [];
  }

  function dataKeyValues(result, declaration) {
    const rows = dataKeyRows(result, declaration);
    const valuePath = declaration?.encoding?.value;
    return valuePath ? rows.map((row) => fieldValue(row, valuePath)) : rows;
  }

  function runtimeError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function visualizerDisplayBase(instance, definition = null) {
    const forbidden = new Set([
      String(instance?.id || "").trim(),
      String(instance?.callback || "").trim(),
      String(definition?.id || "").trim(),
    ].filter(Boolean));
    for (const candidate of [instance?.displayName, definition?.label]) {
      const original = typeof candidate === "string" ? candidate.trim() : "";
      if (!original || forbidden.has(original)) continue;
      const forms = window.TradeModuleForms;
      if (typeof forms?.opaqueMachineIdentityKind === "function"
          && forms.opaqueMachineIdentityKind(original)) continue;
      const value = typeof forms?.userFacingText === "function"
        ? forms.userFacingText(original, "")
        : original;
      if (value) return value;
    }
    return "Data Display";
  }

  function assignVisualizerDisplayLabels(plans) {
    const counts = new Map();
    const bases = plans.map((plan) => {
      const base = visualizerDisplayBase(plan.instance, plan.definition);
      counts.set(base, (counts.get(base) || 0) + 1);
      return base;
    });
    const seen = new Map();
    plans.forEach((plan, index) => {
      const base = bases[index];
      const ordinal = (seen.get(base) || 0) + 1;
      seen.set(base, ordinal);
      plan.displayLabel = counts.get(base) > 1 ? `${base} ${ordinal}` : base;
    });
  }

  function publicDiagnosticMessage(code, fallbackCode = "renderer-error") {
    const value = String(code || fallbackCode);
    if (value === "instance-load-error") return "Chart data could not be loaded.";
    if (value === "instance-planning-error" || value === "dependency-planning-error") {
      return "Chart data dependencies could not be planned.";
    }
    if (value === "missing-instance-result") return "Chart data is not available yet.";
    if ([
      "missing-overlay-target", "ambiguous-capability-target",
      "capability-attribute-mismatch", "invalid-capability-binding",
    ].includes(value)) return "A visible compatible target display is required.";
    if ([
      "unknown-visualizer", "unknown-renderer", "invalid-renderer-contract",
      "renderer-api-version-mismatch", "invalid-interaction-contract",
    ].includes(value)) return "This data display is unavailable.";
    if (value === "duplicate-visualizer-id") return "The chart contains duplicate data displays.";
    if (value === "time-domain-mismatch") return "This data display uses a different time domain.";
    if ([
      "unknown-input", "unknown-input-source", "input-schema-mismatch",
      "missing-explicit-time", "invalid-input-value", "invalid-series-offset",
      "invalid-time-domain-capability",
    ].includes(value)) return "The data display input does not match its contract.";
    return fallbackCode === "renderer-prepare-error"
      ? "The data display could not be prepared."
      : "The data display could not be rendered.";
  }

  const unsafeMapKeys = new Set(["__proto__", "prototype", "constructor"]);
  const capabilityIdentifierPattern = /^[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$/;

  function requireRuntimeInstanceId(instance) {
    const id = instance?.id;
    if (typeof id !== "string" || !id || unsafeMapKeys.has(id)) {
      throw runtimeError("invalid-instance-id", "Visualizer identity is unsafe or missing.");
    }
    return id;
  }

  function requireExactHostFields(value, fields, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw runtimeError("invalid-capability-contract", `${label} must be an object.`);
    }
    const names = Object.keys(value);
    if (names.length !== fields.length || names.some((name) => !fields.includes(name))) {
      throw runtimeError("invalid-capability-contract", `${label} has an invalid shape.`);
    }
  }

  function requireCapabilityIdentifier(value, label) {
    if (typeof value !== "string" || !capabilityIdentifierPattern.test(value)
        || unsafeMapKeys.has(value)) {
      throw runtimeError(
        "invalid-capability-contract",
        `${label} must be a safe ASCII capability identifier.`,
      );
    }
    return value;
  }

  function requireCapabilityStringMap(value, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw runtimeError("invalid-capability-contract", `${label} must be an object.`);
    }
    const result = Object.create(null);
    for (const name of Object.keys(value)) {
      requireCapabilityIdentifier(name, `${label} key '${name}'`);
      const parameter = requireCapabilityIdentifier(value[name], `${label}.${name} parameter`);
      result[name] = parameter;
    }
    return result;
  }

  function requireCapabilityParameterBindings(definition, provides, requires) {
    const paramsSchema = definition?.paramsSchema;
    if (!paramsSchema || typeof paramsSchema !== "object" || Array.isArray(paramsSchema)
        || paramsSchema.type !== "object"
        || !paramsSchema.properties || typeof paramsSchema.properties !== "object"
        || Array.isArray(paramsSchema.properties)
        || !Array.isArray(paramsSchema.required)
        || paramsSchema.additionalProperties !== false) {
      throw runtimeError(
        "invalid-capability-contract",
        "Visualizer paramsSchema must be an exact object contract.",
      );
    }
    const required = new Set(paramsSchema.required);
    const mappings = provides.flatMap((descriptor) => (
      Object.entries(descriptor.attributes).map(([attribute, parameter]) => ({
        path: `Provided capability '${descriptor.name}' attribute '${attribute}'`, parameter,
      }))
    ));
    requires.forEach((descriptor) => {
      mappings.push({
        path: `Required capability '${descriptor.name}' binding`,
        parameter: descriptor.bindingParam,
      });
      Object.entries(descriptor.matches).forEach(([attribute, parameter]) => mappings.push({
        path: `Required capability '${descriptor.name}' match '${attribute}'`, parameter,
      }));
    });
    for (const { path, parameter } of mappings) {
      if (!hasOwn(paramsSchema.properties, parameter) || !required.has(parameter)) {
        throw runtimeError(
          "invalid-capability-contract",
          `${path} must map to a required paramsSchema property.`,
        );
      }
    }
    for (const descriptor of requires) {
      const property = paramsSchema.properties[descriptor.bindingParam];
      if (!property || typeof property !== "object" || Array.isArray(property)
          || property.type !== "string") {
        throw runtimeError(
          "invalid-capability-contract",
          `Required capability '${descriptor.name}' binding parameter must have string schema type.`,
        );
      }
    }
  }

  function definitionCapabilities(definition) {
    const capabilities = definition?.capabilities;
    requireExactHostFields(capabilities, ["provides", "requires", "interactions"], "Visualizer capabilities");
    if (!Array.isArray(capabilities.provides) || !Array.isArray(capabilities.requires)
        || !Array.isArray(capabilities.interactions)) {
      throw runtimeError("invalid-capability-contract", "Visualizer capability collections must be arrays.");
    }
    const names = new Set();
    const provides = capabilities.provides.map((descriptor, index) => {
      requireExactHostFields(descriptor, ["name", "kind", "attributes"], `Provided capability ${index}`);
      requireCapabilityIdentifier(descriptor.name, `Provided capability ${index}.name`);
      requireCapabilityIdentifier(descriptor.kind, `Provided capability ${index}.kind`);
      if (names.has(descriptor.name)) {
        throw runtimeError("invalid-capability-contract", `Capability name '${descriptor.name}' is duplicated.`);
      }
      names.add(descriptor.name);
      return Object.freeze({
        name: descriptor.name,
        kind: descriptor.kind,
        attributes: deepFreeze(requireCapabilityStringMap(descriptor.attributes, `Provided capability '${descriptor.name}' attributes`)),
      });
    });
    const providedNames = provides.map((descriptor) => descriptor.name);
    if (!jsonValuesEqual(providedNames, [...providedNames].sort())) {
      throw runtimeError("invalid-capability-contract", "Provided capabilities must be sorted by unique name.");
    }
    const requires = capabilities.requires.map((descriptor, index) => {
      requireExactHostFields(descriptor, ["name", "kind", "bindingParam", "matches"], `Required capability ${index}`);
      requireCapabilityIdentifier(descriptor.name, `Required capability ${index}.name`);
      requireCapabilityIdentifier(descriptor.kind, `Required capability ${index}.kind`);
      requireCapabilityIdentifier(descriptor.bindingParam, `Required capability ${index}.bindingParam`);
      if (names.has(descriptor.name)) {
        throw runtimeError("invalid-capability-contract", `Capability name '${descriptor.name}' is duplicated.`);
      }
      names.add(descriptor.name);
      return Object.freeze({
        name: descriptor.name,
        kind: descriptor.kind,
        bindingParam: descriptor.bindingParam,
        matches: deepFreeze(requireCapabilityStringMap(descriptor.matches, `Required capability '${descriptor.name}' matches`)),
      });
    });
    const requiredNames = requires.map((descriptor) => descriptor.name);
    if (!jsonValuesEqual(requiredNames, [...requiredNames].sort())) {
      throw runtimeError("invalid-capability-contract", "Required capabilities must be sorted by unique name.");
    }
    const interactions = capabilities.interactions.map((value, index) => {
      return requireCapabilityIdentifier(value, `Interaction capability ${index}`);
    });
    if (!jsonValuesEqual(interactions, [...new Set(interactions)].sort())) {
      throw runtimeError("invalid-capability-contract", "Interaction capabilities must be sorted and unique.");
    }
    requireCapabilityParameterBindings(definition, provides, requires);
    return Object.freeze({ provides: Object.freeze(provides), requires: Object.freeze(requires), interactions: Object.freeze(interactions) });
  }

  function capabilityAttributeValues(descriptor, params) {
    const values = Object.create(null);
    for (const [attribute, parameter] of Object.entries(descriptor.attributes || {})) {
      if (!hasOwn(params, parameter)) {
        throw runtimeError(
          "invalid-capability-binding",
          `Provided capability '${descriptor.name}' attribute '${attribute}' is not explicitly bound.`,
        );
      }
      values[attribute] = structuredClone(params[parameter]);
    }
    return deepFreeze(values);
  }

  function planTimeDomainValues(plan) {
    return [...new Set(plan.capabilities.provides.flatMap((descriptor) => (
      hasOwn(descriptor.attributes, "time-domain")
        ? [plan.params[descriptor.attributes["time-domain"]]]
        : []
    )))];
  }

  function rendererIdForDefinition(definition) {
    if (definition?.renderer?.apiVersion !== 1 || typeof definition?.renderer?.id !== "string") {
      throw runtimeError("invalid-renderer-contract", `Visualizer '${definition?.id || "unknown"}' has no renderer apiVersion 1 contract.`);
    }
    if (definition.renderer.id !== definition.id) {
      throw runtimeError("invalid-renderer-contract", `Visualizer '${definition.id}' must use its identically named host renderer adapter.`);
    }
    return definition.renderer.id;
  }

  function effectiveInstanceParams(definition, instance) {
    const params = structuredClone(instance?.params || {});
    for (const field of definition?.params || []) {
      if (params[field.name] === undefined && field.default !== undefined) {
        params[field.name] = structuredClone(field.default);
      }
    }
    return deepFreeze(params);
  }

  function effectivePlanInstance(plan) {
    const instance = structuredClone(plan.instance);
    instance.params = structuredClone(plan.params);
    return deepFreeze(instance);
  }

  function mergeAttenuatedValues(left, right) {
    if (right === undefined) return left;
    if (left === undefined) return right;
    if (left && right && typeof left === "object" && typeof right === "object"
        && !Array.isArray(left) && !Array.isArray(right)) {
      const merged = Object.assign(Object.create(null), left);
      for (const [name, value] of Object.entries(right)) {
        const previous = hasOwn(merged, name) ? merged[name] : undefined;
        merged[name] = mergeAttenuatedValues(previous, value);
      }
      return merged;
    }
    return right;
  }

  function schemaHasLiteralConstraint(schemaValue) {
    const schema = normalizeSchema(schemaValue);
    if (schema === false) return true;
    if (Object.prototype.hasOwnProperty.call(schema, "const") || Array.isArray(schema.enum)) return true;
    return Object.values(schema.properties || {}).some(schemaHasLiteralConstraint)
      || ["allOf", "anyOf", "oneOf"].some((keyword) => (
        (schema[keyword] || []).some(schemaHasLiteralConstraint)
      ));
  }

  function schemaMatchesForAttenuation(value, schemaValue) {
    const schema = normalizeSchema(schemaValue);
    if (schema === false) return false;
    const actual = jsonType(value);
    if (actual === "non-json") return false;
    const allowed = schemaTypes(schema);
    if (!allowed.has(actual) && !(actual === "integer" && allowed.has("number"))) return false;
    if (Object.prototype.hasOwnProperty.call(schema, "const")
        && !jsonValuesEqual(value, schema.const)) return false;
    if (schema.enum && !schema.enum.some((candidate) => jsonValuesEqual(value, candidate))) return false;
    if (actual === "object") {
      if ((schema.required || []).some((name) => !Object.prototype.hasOwnProperty.call(value, name))) return false;
      const properties = Object.entries(schema.properties || {}).sort(([, left], [, right]) => (
        Number(schemaHasLiteralConstraint(right)) - Number(schemaHasLiteralConstraint(left))
      ));
      for (const [name, childSchema] of properties) {
        if (Object.prototype.hasOwnProperty.call(value, name)
            && !schemaMatchesForAttenuation(value[name], childSchema)) return false;
      }
    } else if (actual === "array" && Object.prototype.hasOwnProperty.call(schema, "items")) {
      if (value.some((item) => !schemaMatchesForAttenuation(item, schema.items))) return false;
    }
    const allOf = [...(schema.allOf || [])].sort((left, right) => (
      Number(schemaHasLiteralConstraint(right)) - Number(schemaHasLiteralConstraint(left))
    ));
    if (allOf.some((branch) => !schemaMatchesForAttenuation(value, branch))) return false;
    if (schema.anyOf && !schema.anyOf.some((branch) => schemaMatchesForAttenuation(value, branch))) return false;
    if (schema.oneOf && schema.oneOf.filter((branch) => schemaMatchesForAttenuation(value, branch)).length !== 1) return false;
    return true;
  }

  function attenuateInputValue(value, schemaValue) {
    const schema = normalizeSchema(schemaValue);
    if (schema === false) return undefined;
    let projected;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      projected = Object.create(null);
      const properties = schema.properties || {};
      for (const [name, childSchema] of Object.entries(properties)) {
        if (!Object.prototype.hasOwnProperty.call(value, name)) continue;
        const child = attenuateInputValue(value[name], childSchema);
        if (child !== undefined) projected[name] = child;
      }
      if (Object.prototype.hasOwnProperty.call(schema, "additionalProperties")) {
        const additional = schema.additionalProperties;
        if (additional !== false) {
          for (const name of Object.keys(value)) {
            if (Object.prototype.hasOwnProperty.call(properties, name)) continue;
            const child = additional === true
              ? structuredClone(value[name])
              : attenuateInputValue(value[name], additional);
            if (child !== undefined) projected[name] = child;
          }
        }
      }
    } else if (Array.isArray(value)) {
      projected = Object.prototype.hasOwnProperty.call(schema, "items")
        ? value.map((item) => attenuateInputValue(item, schema.items))
        : [];
    } else {
      projected = structuredClone(value);
    }

    for (const branch of schema.allOf || []) {
      projected = mergeAttenuatedValues(projected, attenuateInputValue(value, branch));
    }
    for (const keyword of ["anyOf", "oneOf"]) {
      if (!Array.isArray(schema[keyword])) continue;
      const matching = schema[keyword].filter((branch) => {
        try { return schemaMatchesForAttenuation(value, branch); } catch { return false; }
      });
      if (keyword === "oneOf" && matching.length !== 1) continue;
      for (const branch of matching) {
        projected = mergeAttenuatedValues(projected, attenuateInputValue(value, branch));
      }
    }
    return projected;
  }

  function bindVisualizerInputs(result, spec, definition, params) {
    const inputs = Object.create(null);
    for (const [portName, port] of Object.entries(definition?.inputPorts || {})) {
      const dataKey = params?.[portName];
      if (typeof dataKey !== "string" || !dataKey.trim()) {
        if (port?.required === false) continue;
        throw runtimeError("unknown-input", `Input '${portName}' is not explicitly bound.`);
      }
      const declaration = resolveDataKeyDeclaration(result, spec, dataKey);
      if (!declaration) {
        throw runtimeError("unknown-input", `Input '${portName}' references unknown DataKey '${dataKey}'.`);
      }
      if (!visualizerSchemasCompatible(declaration.schema || {}, port.schema || {})) {
        throw runtimeError("input-schema-mismatch", `Input '${portName}' DataKey '${dataKey}' does not satisfy the visualizer contract.`);
      }
      inputs[portName] = frozenClone(
        dataKeyValues(result, declaration).map((value) => attenuateInputValue(value, port.schema || {})),
      );
    }
    return deepFreeze(inputs);
  }

  function rendererPreparePayload(plan) {
    return Object.freeze({
      instanceId: plan.instance.id,
      callback: plan.instance.callback,
      params: plan.params,
      inputs: plan.inputs,
    });
  }

  function instanceLoadMessage(error) {
    if (typeof error === "string" && error.trim()) return error;
    if (error && typeof error.message === "string" && error.message.trim()) return error.message;
    return "Visualizer data could not be loaded.";
  }

  function resultForVisualizer(result, instance, definition) {
    if (!result || typeof result !== "object"
        || !Object.prototype.hasOwnProperty.call(result, "instanceResults")) return result;
    const visualizerId = instance?.id || "";
    const errors = result.errors && typeof result.errors === "object" ? result.errors : {};
    if (Object.prototype.hasOwnProperty.call(errors, visualizerId) && errors[visualizerId] != null) {
      const loadError = errors[visualizerId];
      const code = loadError?.code === "instance-planning-error"
        ? "instance-planning-error"
        : "instance-load-error";
      throw runtimeError(code, instanceLoadMessage(loadError));
    }
    const slices = result.instanceResults && typeof result.instanceResults === "object"
      ? result.instanceResults : {};
    if (Object.prototype.hasOwnProperty.call(slices, visualizerId) && slices[visualizerId]) {
      return slices[visualizerId];
    }
    if (!Object.keys(definition?.inputPorts || {}).length) return { dataKeys: {}, cycles: [] };
    throw runtimeError("missing-instance-result", `No isolated Result slice was supplied for Visualizer '${visualizerId}'.`);
  }

  function prepareVisualizerPlan(result, spec, instance) {
    requireRuntimeInstanceId(instance);
    const definition = visualizerDefinitionById(instance?.callback);
    if (!definition) {
      throw runtimeError("unknown-visualizer", `Visualizer definition '${instance?.callback || "unknown"}' is unavailable.`);
    }
    const rendererId = rendererIdForDefinition(definition);
    const renderer = hostRenderers[rendererId];
    if (!renderer || typeof renderer.draw !== "function") {
      throw runtimeError("unknown-renderer", `Renderer '${rendererId}' is unavailable.`);
    }
    if (renderer.apiVersion !== definition.renderer.apiVersion) {
      throw runtimeError(
        "renderer-api-version-mismatch",
        `Renderer '${rendererId}' apiVersion does not match its Definition.`,
      );
    }
    const params = effectiveInstanceParams(definition, instance);
    const capabilities = definitionCapabilities(definition);
    capabilities.provides.forEach((descriptor) => capabilityAttributeValues(descriptor, params));
    capabilities.requires.forEach((descriptor) => {
      if (!hasOwn(params, descriptor.bindingParam)
          || typeof params[descriptor.bindingParam] !== "string"
          || !params[descriptor.bindingParam].trim()) {
        throw runtimeError(
          "invalid-capability-binding",
          `Required capability '${descriptor.name}' is not explicitly bound.`,
        );
      }
      Object.values(descriptor.matches).forEach((parameter) => {
        if (!hasOwn(params, parameter)) {
          throw runtimeError(
            "invalid-capability-binding",
            `Required capability '${descriptor.name}' match parameter '${parameter}' is unavailable.`,
          );
        }
      });
    });
    const inputs = bindVisualizerInputs(
      resultForVisualizer(result, instance, definition), spec, definition, params,
    );
    const plan = {
      instance,
      definition,
      rendererId,
      renderer,
      capabilities,
      params,
      inputs,
      prepared: null,
      displayLabel: visualizerDisplayBase(instance, definition),
    };
    const prepared = typeof renderer.prepare === "function"
      ? renderer.prepare(rendererPreparePayload(plan))
      : null;
    plan.prepared = frozenClone(prepared);
    return plan;
  }

  function planDiagnostic(instance, error, fallbackCode = "renderer-error") {
    const definition = visualizerDefinitionById(instance?.callback);
    const code = error?.code || fallbackCode;
    return {
      code,
      visualizerId: instance?.id || "",
      renderer: instance?.callback || "",
      label: visualizerDisplayBase(instance, definition),
      message: publicDiagnosticMessage(code, fallbackCode),
    };
  }

  function paneTimeInfo(result, pane, spec = {}) {
    const diagnostics = [];
    const values = [];
    let explicitIntraday = false;
    let timeDomainId = null;
    const duplicateIds = duplicatePaneVisualizerIds(pane);
    for (const instance of pane?.visualizers || []) {
      if (!instance || instance.visible === false) continue;
      let plan;
      try {
        requireUniquePaneVisualizerInstance(instance, duplicateIds);
        plan = prepareVisualizerPlan(result, spec, instance);
      } catch (error) {
        diagnostics.push(planDiagnostic(instance, error, "renderer-prepare-error"));
        continue;
      }
      const planDomains = planTimeDomainValues(plan);
      if (!planDomains.length) continue;
      if (planDomains.length !== 1 || typeof planDomains[0] !== "string" || !planDomains[0].trim()) {
        diagnostics.push(planDiagnostic(instance, runtimeError(
          "invalid-time-domain-capability",
          "A visible provided resource must expose exactly one non-empty time-domain attribute.",
        )));
        continue;
      }
      const planTimeDomainId = planDomains[0];
      if (timeDomainId === null) timeDomainId = planTimeDomainId;
      if (planTimeDomainId !== timeDomainId) {
        diagnostics.push(planDiagnostic(instance, runtimeError(
          "time-domain-mismatch",
          `Provided time-domain '${planTimeDomainId}' cannot share a pane with '${timeDomainId}'.`,
        )));
        continue;
      }
      if (typeof plan.renderer.timeDomain !== "function") {
        diagnostics.push(planDiagnostic(instance, runtimeError("missing-time-domain-provider", "Renderer does not expose its explicit time domain.")));
        continue;
      }
      let rawValues;
      try {
        rawValues = plan.renderer.timeDomain(Object.freeze({
          instanceId: instance.id,
          callback: instance.callback,
          params: plan.params,
          inputs: plan.inputs,
          prepared: plan.prepared,
        }));
      } catch (error) {
        diagnostics.push(planDiagnostic(instance, error, "renderer-time-domain-error"));
        continue;
      }
      for (const raw of rawValues || []) {
        const value = chartTime(raw);
        if (!validChartTime(value)) continue;
        const date = chartDateFromTime(value);
        if (date && (date.getUTCHours() || date.getUTCMinutes() || date.getUTCSeconds())) explicitIntraday = true;
        if (typeof value === "number" && Number.isFinite(value)) values.push(value);
      }
    }
    const sorted = [...new Set(values)].sort((a, b) => a - b);
    const subDailySpacing = sorted.some((value, index) => index > 0 && value - sorted[index - 1] > 0 && value - sorted[index - 1] < 86400);
    return {
      start: sorted.length ? sorted[0] : null,
      end: sorted.length ? sorted[sorted.length - 1] : null,
      showTime: explicitIntraday || subDailySpacing,
      timeDomainId,
      diagnostics,
    };
  }

  function lineStyle(library, value) {
    if (typeof value === "number") return value;
    const key = String(value || "solid").toLowerCase();
    if (key === "dashed") return library.LineStyle?.Dashed ?? 2;
    if (key === "dotted") return library.LineStyle?.Dotted ?? 1;
    return library.LineStyle?.Solid ?? 0;
  }

  function exactSceneObject(value, fields, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw runtimeError("invalid-canvas-scene", `${label} must be an object.`);
    }
    const names = Object.keys(value);
    if (names.some((name) => !fields.includes(name))) {
      throw runtimeError("invalid-canvas-scene", `${label} contains an unknown field.`);
    }
    return names;
  }

  function normalizeScenePoint(value, label) {
    const fields = exactSceneObject(value, ["x", "y"], label);
    if (fields.length !== 2 || !fields.includes("x") || !fields.includes("y")
        || !validChartTime(chartTime(value.x)) || !finiteNumber(value.y)) {
      throw runtimeError("invalid-canvas-scene", `${label} requires explicit finite x/y coordinates.`);
    }
    return Object.freeze({ x: structuredClone(value.x), y: value.y });
  }

  function normalizeSceneStroke(value, label) {
    const fields = exactSceneObject(value, ["color", "width"], label);
    if (fields.length !== 2 || typeof value.color !== "string" || !value.color.trim()
        || !finiteNumber(value.width) || value.width <= 0) {
      throw runtimeError("invalid-canvas-scene", `${label} requires color and a positive finite width.`);
    }
    return Object.freeze({ color: value.color, width: value.width });
  }

  function normalizeSceneFill(value, label) {
    const fields = exactSceneObject(value, ["color"], label);
    if (fields.length !== 1 || typeof value.color !== "string" || !value.color.trim()) {
      throw runtimeError("invalid-canvas-scene", `${label} requires a non-empty color.`);
    }
    return Object.freeze({ color: value.color });
  }

  function normalizeSceneTextStyle(value, label) {
    const fields = exactSceneObject(value, ["color", "font"], label);
    if (fields.length !== 2 || typeof value.color !== "string" || !value.color.trim()
        || typeof value.font !== "string" || !value.font.trim()) {
      throw runtimeError("invalid-canvas-scene", `${label} requires non-empty color and font values.`);
    }
    return Object.freeze({ color: value.color, font: value.font });
  }

  function normalizeCanvasScene(value) {
    const fields = exactSceneObject(value, ["apiVersion", "nodes"], "Canvas scene");
    if (fields.length !== 2 || value.apiVersion !== 1 || !Array.isArray(value.nodes)) {
      throw runtimeError("invalid-canvas-scene", "Canvas scene apiVersion 1 and nodes are required.");
    }
    const nodes = value.nodes.map((node, index) => {
      if (!node || typeof node !== "object" || Array.isArray(node)) {
        throw runtimeError("invalid-canvas-scene", `Canvas scene node ${index} must be an object.`);
      }
      if (node.kind === "path") {
        const nodeFields = exactSceneObject(node, ["kind", "points", "closed", "stroke", "fill"], `Path node ${index}`);
        if (!nodeFields.includes("kind") || !nodeFields.includes("points") || !nodeFields.includes("closed")
            || node.kind !== "path" || !Array.isArray(node.points) || node.points.length < 2
            || typeof node.closed !== "boolean" || !nodeFields.includes("stroke") && !nodeFields.includes("fill")) {
          throw runtimeError("invalid-canvas-scene", `Path node ${index} has an invalid shape.`);
        }
        return Object.freeze({
          kind: "path",
          points: Object.freeze(node.points.map((point, pointIndex) => normalizeScenePoint(point, `Path node ${index} point ${pointIndex}`))),
          closed: node.closed,
          ...(nodeFields.includes("stroke") ? { stroke: normalizeSceneStroke(node.stroke, `Path node ${index} stroke`) } : {}),
          ...(nodeFields.includes("fill") ? { fill: normalizeSceneFill(node.fill, `Path node ${index} fill`) } : {}),
        });
      }
      if (node.kind === "text") {
        const nodeFields = exactSceneObject(node, ["kind", "anchor", "text", "style"], `Text node ${index}`);
        if (nodeFields.length !== 4 || node.kind !== "text" || typeof node.text !== "string") {
          throw runtimeError("invalid-canvas-scene", `Text node ${index} has an invalid shape.`);
        }
        return Object.freeze({
          kind: "text",
          anchor: normalizeScenePoint(node.anchor, `Text node ${index} anchor`),
          text: node.text,
          style: normalizeSceneTextStyle(node.style, `Text node ${index} style`),
        });
      }
      throw runtimeError("invalid-canvas-scene", `Canvas scene node ${index} has unknown kind '${String(node.kind)}'.`);
    });
    return Object.freeze({ apiVersion: 1, nodes: Object.freeze(nodes) });
  }

  function createCanvasSceneArtifact(chart, series, sceneValue, reportDiagnostic) {
    if (typeof series?.attachPrimitive !== "function" || typeof series?.detachPrimitive !== "function") {
      throw runtimeError("host-capability-missing", "The explicit target cannot attach Canvas scene primitives.");
    }
    let accepted = normalizeCanvasScene(sceneValue);
    let displayed = accepted;
    let requestUpdate = null;
    let detached = false;
    let lastDrawError = "";
    const project = (point) => ({
      x: chart.timeScale().timeToCoordinate(chartTime(point.x)),
      y: series.priceToCoordinate(point.y),
    });
    const drawScene = (context) => {
      for (const node of displayed.nodes) {
        if (node.kind === "path") {
          const points = node.points.map(project);
          if (points.some((point) => !finiteNumber(point.x) || !finiteNumber(point.y))) continue;
          context.save();
          context.beginPath();
          context.moveTo(points[0].x, points[0].y);
          points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
          if (node.closed) context.closePath();
          if (node.fill) {
            context.fillStyle = node.fill.color;
            context.fill();
          }
          if (node.stroke) {
            context.strokeStyle = node.stroke.color;
            context.lineWidth = node.stroke.width;
            context.stroke();
          }
          context.restore();
          continue;
        }
        const anchor = project(node.anchor);
        if (!finiteNumber(anchor.x) || !finiteNumber(anchor.y)) continue;
        context.save();
        context.fillStyle = node.style.color;
        context.font = node.style.font;
        context.textAlign = "left";
        context.textBaseline = "alphabetic";
        context.fillText(node.text, anchor.x, anchor.y);
        context.restore();
      }
    };
    const paneRenderer = Object.freeze({
      draw(target) {
        try {
          if (!target || typeof target.useMediaCoordinateSpace !== "function") {
            throw runtimeError("host-capability-missing", "Canvas media-coordinate rendering is unavailable.");
          }
          target.useMediaCoordinateSpace(({ context }) => drawScene(context));
          lastDrawError = "";
        } catch (error) {
          const message = error?.message || "Canvas scene drawing failed.";
          if (message !== lastDrawError) reportDiagnostic(message);
          lastDrawError = message;
        }
      },
    });
    const paneView = Object.freeze({
      zOrder() { return "top"; },
      renderer() { return paneRenderer; },
    });
    const primitive = Object.freeze({
      attached(parameter) { requestUpdate = parameter?.requestUpdate || null; },
      detached() { requestUpdate = null; },
      updateAllViews() {},
      paneViews() { return [paneView]; },
    });
    series.attachPrimitive(primitive);
    const replace = (next) => {
      if (detached) throw runtimeError("canvas-scene-detached", "Canvas scene is detached.");
      displayed = next;
      requestUpdate?.();
    };
    return Object.freeze({
      kind: "canvas-scene",
      stage(nextScene) {
        const next = normalizeCanvasScene(nextScene);
        const previous = displayed;
        let committed = false;
        return Object.freeze({
          commit() {
            committed = true;
            try {
              replace(next);
            } catch (error) {
              let rollbackError = null;
              try { replace(previous); } catch (failure) { rollbackError = failure; }
              committed = false;
              if (rollbackError) {
                throw runtimeError(
                  "drawing-preview-rollback-failed",
                  rollbackError?.message || "Canvas scene preview rollback failed.",
                );
              }
              throw error;
            }
          },
          rollback() {
            if (!committed) return;
            replace(previous);
            committed = false;
          },
        });
      },
      accept() { accepted = displayed; },
      restore() { replace(accepted); },
      detach() {
        if (detached) return;
        series.detachPrimitive(primitive);
        detached = true;
        requestUpdate = null;
      },
    });
  }

  // Renderer adapters are trusted host code.  Definitions may select one of
  // these ids, but application/plugin code cannot install executable
  // JavaScript into this realm.
  const hostRenderers = Object.create(null);
  const rendererLabels = {};
  const rendererParams = {};

  function installHostRenderer(id, renderer) {
    if (typeof id !== "string" || !id || !renderer || typeof renderer !== "object") {
      throw new Error("Renderer registration requires an id and descriptor.");
    }
    hostRenderers[id] = Object.freeze({ ...renderer });
    rendererLabels[id] = renderer.label || id;
    rendererParams[id] = renderer.params || [];
  }

  function drawCallbackCatalog() {
    return Object.keys(hostRenderers).sort().map((id) => ({
      id,
      label: rendererLabels[id] || id,
      params: rendererParams[id] || [],
    }));
  }

  function requireFinitePairedTimes(values, times, label, requestedOffsetBars = 0) {
    const offsetBars = requireOffsetBars(requestedOffsetBars, label);
    const timeCount = Array.isArray(times) ? times.length : 0;
    (values || []).forEach((value, index) => {
      if (finiteNumber(value) && !validChartTime(chartTime(times?.[index]))) {
        throw runtimeError("missing-explicit-time", `${label} has a numeric value without an explicit valid time.`);
      }
      const targetIndex = shiftedTimeIndex(index, offsetBars, timeCount);
      if (finiteNumber(value) && targetIndex >= 0
          && !validChartTime(chartTime(times[targetIndex]))) {
        throw runtimeError("missing-explicit-time", `${label} offset target has no explicit valid time.`);
      }
    });
    return offsetBars;
  }

  installHostRenderer("ohlc.candles", {
    apiVersion: 1,
    label: "Candles",
    prepare({ inputs }) {
      for (const value of inputs.dataKey || []) {
        if (value == null || value.complete === false) continue;
        if (typeof value !== "object" || !validChartTime(chartTime(value.eventTime))) {
          throw runtimeError("missing-explicit-time", "Candles require eventTime on every complete OHLC value.");
        }
        if (![value.open, value.high, value.low, value.close].every(finiteNumber)) {
          throw runtimeError("invalid-input-value", "Candles require finite open/high/low/close values.");
        }
      }
      return { points: completeCandlePoints(inputs.dataKey || []) };
    },
    timeDomain({ prepared }) {
      return (prepared?.points || []).map((point) => point.time);
    },
    draw({ displayLabel, params, prepared, host }) {
      host.createSeries("series", "candlestick", {
        title: displayLabel,
        upColor: params.upColor,
        downColor: params.downColor,
        borderUpColor: params.upColor,
        borderDownColor: params.downColor,
        wickUpColor: params.upColor,
        wickDownColor: params.downColor,
      }, prepared.points);
    },
  });

  installHostRenderer("series.line", {
    apiVersion: 1,
    label: "Line",
    prepare({ inputs, params }) {
      const offsetBars = requireFinitePairedTimes(inputs.dataKey, inputs.timeKey, "Line", params.offsetBars);
      return { points: sparseLinePoints(inputs.dataKey, inputs.timeKey, offsetBars) };
    },
    timeDomain({ prepared }) { return (prepared?.points || []).map((point) => point.time); },
    draw({ params, prepared, host }) {
      host.createSeries("series", "line", {
        color: params.color,
        lineWidth: params.lineWidth,
        lineStyle: host.lineStyle(params.lineStyle),
        priceLineVisible: false,
        lastValueVisible: true,
      }, prepared.points);
    },
  });

  installHostRenderer("series.scatter", {
    apiVersion: 1,
    label: "Scatter",
    prepare({ inputs }) {
      requireFinitePairedTimes(inputs.dataKey, inputs.timeKey, "Scatter");
      return { points: sparseLinePoints(inputs.dataKey, inputs.timeKey) };
    },
    timeDomain({ prepared }) { return (prepared?.points || []).map((point) => point.time); },
    draw({ params, prepared, host }) {
      host.createSeries("series", "line", {
        color: params.color,
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: params.pointRadius,
        priceLineVisible: false,
        lastValueVisible: true,
      }, prepared.points);
    },
  });

  installHostRenderer("series.histogram", {
    apiVersion: 1,
    label: "Histogram",
    prepare({ inputs, params }) {
      const offsetBars = requireFinitePairedTimes(inputs.dataKey, inputs.timeKey, "Histogram", params.offsetBars);
      const points = sparseLinePoints(inputs.dataKey, inputs.timeKey, offsetBars).map((point) => ({
        ...point,
        color: point.value >= 0 ? params.positiveColor : params.negativeColor,
      }));
      return { points };
    },
    timeDomain({ prepared }) { return (prepared?.points || []).map((point) => point.time); },
    draw({ params, prepared, host }) {
      host.createSeries("series", "histogram", {
        color: params.color,
        priceLineVisible: false,
        lastValueVisible: true,
      }, prepared.points);
    },
  });

  installHostRenderer("overlay.markers", {
    apiVersion: 1,
    label: "Markers",
    prepare({ inputs }) {
      const markers = [];
      (inputs.dataKey || []).forEach((event, index) => {
        if (event == null) return;
        const time = chartTime(inputs.timeKey?.[index]);
        if (!validChartTime(time)) throw runtimeError("missing-explicit-time", "Marker data requires an explicit valid time.");
        const side = String(event.side || "default").toLowerCase();
        markers.push({
          time,
          position: event.position || (side === "buy" ? "belowBar" : "aboveBar"),
          color: event.color || "#475569",
          shape: event.shape || "circle",
          text: event.reason || side,
        });
      });
      return { markers: sortByTime(markers) };
    },
    draw({ prepared, host }) {
      host.attachMarkers("target", prepared.markers);
    },
  });

  installHostRenderer("overlay.priceLine", {
    apiVersion: 1,
    label: "Price Line",
    prepare({ inputs, params }) {
      if (params.reducer !== "latest") throw runtimeError("unsupported-reducer", "Price Line reducer must be 'latest'.");
      requireFinitePairedTimes(inputs.dataKey, inputs.timeKey, "Price Line");
      const points = sparseLinePoints(inputs.dataKey, inputs.timeKey);
      return { value: points.length ? points[points.length - 1].value : null };
    },
    draw({ displayLabel, params, prepared, host }) {
      if (!finiteNumber(prepared.value)) return;
      host.attachPriceLine("target", {
        price: prepared.value,
        color: params.color,
        lineWidth: params.lineWidth,
        lineStyle: host.lineStyle(params.lineStyle),
        axisLabelVisible: true,
        title: displayLabel,
      });
    },
  });

  function drawingEventPoint(event) {
    const point = event?.domain;
    return point && validChartTime(chartTime(point.x)) && finiteNumber(point.y)
      ? { time: chartDateFromTime(point.x)?.toISOString(), price: point.y }
      : null;
  }

  function drawingScenePoint(point) {
    return { x: point.time, y: point.price };
  }

  const DRAWING_DRAG_THRESHOLD = 4;

  function drawingDragThresholdCrossed(start, current) {
    return !!start && !!current
      && finiteNumber(start.x) && finiteNumber(start.y)
      && finiteNumber(current.x) && finiteNumber(current.y)
      && Math.hypot(current.x - start.x, current.y - start.y) >= DRAWING_DRAG_THRESHOLD;
  }

  function samePointer(state, event) {
    return state.pointerId === event.pointer.pointerId;
  }

  function beginTwoPointDrawing(parameterName, bindings, event) {
    if (event.phase !== "pointerdown") return null;
    const point = drawingEventPoint(event);
    if (!point) return null;
    const params = { targetVisualizerId: bindings.target, [parameterName]: [point, point] };
    return {
      status: "active",
      state: {
        stage: "first",
        pointerId: event.pointer.pointerId,
        first: point,
        current: point,
        startCanvas: event.canvas,
        dragged: false,
      },
      draftParams: params,
    };
  }

  function reduceTwoPointDrawing(parameterName, bindings, state, event) {
    if (event.phase === "pointercancel") return { status: "cancel" };
    const point = drawingEventPoint(event);
    if (event.phase.startsWith("pointer") && !point) return null;
    if (state.stage === "first") {
      if (!samePointer(state, event)) return null;
      if (event.phase === "pointermove") {
        const dragged = state.dragged || drawingDragThresholdCrossed(state.startCanvas, event.canvas);
        return {
          status: "active",
          state: { ...state, current: point, dragged },
          draftParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
        };
      }
      if (event.phase === "pointerup") {
        if (state.dragged || drawingDragThresholdCrossed(state.startCanvas, event.canvas)) {
          return {
            status: "commit",
            commitParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
          };
        }
        return {
          status: "active",
          state: { stage: "hover", pointerId: null, first: state.first, current: point },
          draftParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
        };
      }
      return null;
    }
    if (state.stage === "hover") {
      if (event.phase === "pointermove") {
        return {
          status: "active",
          state: { ...state, current: point },
          draftParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
        };
      }
      if (event.phase === "pointerdown") {
        return {
          status: "active",
          state: { stage: "second", pointerId: event.pointer.pointerId, first: state.first, current: point },
          draftParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
        };
      }
      return null;
    }
    if (!samePointer(state, event)) return null;
    if (event.phase === "pointermove") {
      return {
        status: "active",
        state: { ...state, current: point },
        draftParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
      };
    }
    if (event.phase === "pointerup") {
      return {
        status: "commit",
        commitParams: { targetVisualizerId: bindings.target, [parameterName]: [state.first, point] },
      };
    }
    return null;
  }

  function pointHandleHit(points, event, project, distanceToShape) {
    const projected = points.map((point) => project(drawingScenePoint(point)));
    if (projected.some((point) => !point || !finiteNumber(point.x) || !finiteNumber(point.y))) return null;
    const distances = projected.map((point) => Math.hypot(event.canvas.x - point.x, event.canvas.y - point.y));
    const handleIndex = distances[0] <= distances[1] ? 0 : 1;
    if (distances[handleIndex] <= 9) return { handleIndex };
    return distanceToShape(projected) <= 8 ? { handleIndex } : null;
  }

  function editPointArrayStart(parameterName, params, hit, event) {
    if (event.phase !== "pointerdown") return null;
    const point = drawingEventPoint(event);
    if (!point || !Number.isInteger(hit?.handleIndex)) return null;
    const next = structuredClone(params);
    next[parameterName][hit.handleIndex] = point;
    return {
      status: "active",
      state: { pointerId: event.pointer.pointerId, handleIndex: hit.handleIndex },
      draftParams: next,
    };
  }

  function editPointArrayReduce(parameterName, params, state, event) {
    if (event.phase === "pointercancel") return { status: "cancel" };
    if (!samePointer(state, event)) return null;
    const point = drawingEventPoint(event);
    if (!point) return null;
    const next = structuredClone(params);
    next[parameterName][state.handleIndex] = point;
    if (event.phase === "pointermove") {
      return { status: "active", state, draftParams: next };
    }
    return event.phase === "pointerup" ? { status: "commit", commitParams: next } : null;
  }

  function twoPointInteraction({ toolId, label, parameterName, hitDistance }) {
    return Object.freeze({
      toolId,
      label,
      consumes: ["chart.pointer"],
      coordinateResource: "target",
      start({ bindings, event }) { return beginTwoPointDrawing(parameterName, bindings, event); },
      reduce({ bindings, state, event }) { return reduceTwoPointDrawing(parameterName, bindings, state, event); },
      hitTest({ params, event, project }) {
        const points = params[parameterName] || [];
        if (points.length !== 2) return null;
        return pointHandleHit(points, event, project, (projected) => hitDistance(event.canvas, projected));
      },
      editStart({ params, hit, event }) { return editPointArrayStart(parameterName, params, hit, event); },
      editReduce({ params, state, event }) { return editPointArrayReduce(parameterName, params, state, event); },
    });
  }

  installHostRenderer("drawing.horizontalLine", {
    apiVersion: 1,
    label: "Horizontal Line",
    interaction: {
      toolId: "horizontal-line",
      label: "Horizontal Line",
      consumes: ["chart.pointer"],
      coordinateResource: "target",
      start({ bindings, event }) {
        if (event.phase !== "pointerdown" || !event.domain || !finiteNumber(event.domain.y)) return null;
        const params = { targetVisualizerId: bindings.target, price: event.domain.y };
        return { status: "active", state: { pointerId: event.pointer.pointerId }, draftParams: params };
      },
      reduce({ bindings, state, event }) {
        if (event.phase === "pointercancel") return { status: "cancel" };
        if (!samePointer(state, event) || !event.domain || !finiteNumber(event.domain.y)) return null;
        const params = { targetVisualizerId: bindings.target, price: event.domain.y };
        if (event.phase === "pointermove") return { status: "active", state, draftParams: params };
        return event.phase === "pointerup" ? { status: "commit", commitParams: params } : null;
      },
      hitTest({ params, event, project }) {
        if (!event.domain || !finiteNumber(params.price)) return null;
        const point = project({ x: event.domain.x, y: params.price });
        return point && finiteNumber(point.y) && Math.abs(event.canvas.y - point.y) <= 7 ? {} : null;
      },
      editStart({ params, event }) {
        if (event.phase !== "pointerdown" || !event.domain || !finiteNumber(event.domain.y)) return null;
        return {
          status: "active",
          state: { pointerId: event.pointer.pointerId },
          draftParams: { ...params, price: event.domain.y },
        };
      },
      editReduce({ params, state, event }) {
        if (event.phase === "pointercancel") return { status: "cancel" };
        if (!samePointer(state, event) || !event.domain || !finiteNumber(event.domain.y)) return null;
        const next = { ...params, price: event.domain.y };
        if (event.phase === "pointermove") return { status: "active", state, draftParams: next };
        return event.phase === "pointerup" ? { status: "commit", commitParams: next } : null;
      },
    },
    prepare({ params }) {
      if (!finiteNumber(params.price)) throw runtimeError("invalid-drawing", "Horizontal Line requires a finite price.");
      return { price: params.price };
    },
    draw({ displayLabel, params, prepared, host }) {
      host.attachPriceLine("target", {
        price: prepared.price,
        color: params.color,
        lineWidth: params.lineWidth,
        axisLabelVisible: true,
        title: displayLabel,
      });
    },
  });

  installHostRenderer("drawing.trendLine", {
    apiVersion: 1,
    label: "Trend Line",
    interaction: twoPointInteraction({
      toolId: "trend-line",
      label: "Trend Line",
      parameterName: "points",
      hitDistance: (pointer, points) => drawingDistanceToSegment(pointer, points[0], points[1]),
    }),
    prepare({ params }) {
      if (!Array.isArray(params.points) || params.points.length !== 2
          || params.points.some((point) => !validChartTime(chartTime(point?.time)) || !finiteNumber(point?.price))) {
        throw runtimeError("invalid-drawing", "Trend Line requires exactly two explicit finite points.");
      }
      return { points: params.points.map(drawingScenePoint) };
    },
    draw({ params, prepared, host }) {
      host.attachCanvasScene("target", {
        apiVersion: 1,
        nodes: [{
          kind: "path", points: prepared.points, closed: false,
          stroke: { color: params.color, width: params.lineWidth },
        }],
      });
    },
  });

  function rectangleEdgeDistance(pointer, points) {
    const corners = [
      points[0], { x: points[1].x, y: points[0].y }, points[1], { x: points[0].x, y: points[1].y },
    ];
    return Math.min(...corners.map((point, index) => (
      drawingDistanceToSegment(pointer, point, corners[(index + 1) % corners.length])
    )));
  }

  function colorWithOpacity(value, opacity) {
    const match = String(value || "").match(/^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/);
    if (!match || !finiteNumber(opacity) || opacity < 0 || opacity > 1) {
      throw runtimeError("invalid-drawing", "Rectangle fill requires a hex color and opacity between zero and one.");
    }
    return `rgba(${Number.parseInt(match[1], 16)}, ${Number.parseInt(match[2], 16)}, ${Number.parseInt(match[3], 16)}, ${opacity})`;
  }

  installHostRenderer("drawing.rectangle", {
    apiVersion: 1,
    label: "Rectangle",
    interaction: twoPointInteraction({
      toolId: "rectangle",
      label: "Rectangle",
      parameterName: "corners",
      hitDistance: rectangleEdgeDistance,
    }),
    prepare({ params }) {
      if (!Array.isArray(params.corners) || params.corners.length !== 2
          || params.corners.some((point) => !validChartTime(chartTime(point?.time)) || !finiteNumber(point?.price))) {
        throw runtimeError("invalid-drawing", "Rectangle requires exactly two explicit finite corners.");
      }
      const [start, end] = params.corners.map(drawingScenePoint);
      return {
        points: [start, { x: end.x, y: start.y }, end, { x: start.x, y: end.y }],
        fill: colorWithOpacity(params.fillColor, params.fillOpacity),
      };
    },
    draw({ params, prepared, host }) {
      host.attachCanvasScene("target", {
        apiVersion: 1,
        nodes: [{
          kind: "path", points: prepared.points, closed: true,
          stroke: { color: params.color, width: params.lineWidth },
          fill: { color: prepared.fill },
        }],
      });
    },
  });

  installHostRenderer("drawing.brush", {
    apiVersion: 1,
    label: "Brush",
    interaction: {
      toolId: "brush",
      label: "Brush",
      consumes: ["chart.pointer"],
      coordinateResource: "target",
      start({ bindings, event }) {
        if (event.phase !== "pointerdown") return null;
        const point = drawingEventPoint(event);
        if (!point) return null;
        return {
          status: "active",
          state: { pointerId: event.pointer.pointerId, points: [point] },
          draftParams: { targetVisualizerId: bindings.target, points: [point, point] },
        };
      },
      reduce({ bindings, state, event }) {
        if (event.phase === "pointercancel") return { status: "cancel" };
        if (!samePointer(state, event)) return null;
        const point = drawingEventPoint(event);
        if (!point) return null;
        if (event.phase !== "pointermove" && event.phase !== "pointerup") return null;
        const points = [...state.points, point];
        const params = { targetVisualizerId: bindings.target, points };
        return event.phase === "pointerup"
          ? { status: "commit", commitParams: params }
          : { status: "active", state: { ...state, points }, draftParams: params };
      },
      hitTest({ params, event, project }) {
        const points = (params.points || []).map((point) => project(drawingScenePoint(point)));
        if (points.length < 2 || points.some((point) => !point || !finiteNumber(point.x) || !finiteNumber(point.y))) return null;
        const distance = Math.min(...points.slice(1).map((point, index) => (
          drawingDistanceToSegment(event.canvas, points[index], point)
        )));
        return distance <= Math.max(8, Number(params.lineWidth || 1) + 4) ? {} : null;
      },
      editStart() { return null; },
      editReduce() { return null; },
    },
    prepare({ params }) {
      if (!Array.isArray(params.points) || params.points.length < 2 || params.points.length > 8192
          || params.points.some((point) => !validChartTime(chartTime(point?.time)) || !finiteNumber(point?.price))) {
        throw runtimeError("invalid-drawing", "Brush requires between two and 8192 explicit finite points.");
      }
      return { points: params.points.map(drawingScenePoint) };
    },
    draw({ params, prepared, host }) {
      host.attachCanvasScene("target", {
        apiVersion: 1,
        nodes: [{
          kind: "path", points: prepared.points, closed: false,
          stroke: { color: params.color, width: params.lineWidth },
        }],
      });
    },
  });

  installHostRenderer("drawing.text", {
    apiVersion: 1,
    label: "Text",
    interaction: {
      toolId: "text",
      label: "Text",
      consumes: ["chart.pointer", "chart.text-input"],
      coordinateResource: "target",
      start({ bindings, event }) {
        if (event.phase !== "pointerdown") return null;
        const anchor = drawingEventPoint(event);
        if (!anchor) return null;
        return {
          status: "active",
          state: { pointerId: event.pointer.pointerId, anchor, text: "" },
          draftParams: { targetVisualizerId: bindings.target, anchor, text: "" },
        };
      },
      reduce({ bindings, state, event }) {
        if (event.phase === "pointercancel" || event.phase === "textcancel") return { status: "cancel" };
        if (event.phase === "pointerup" && samePointer(state, event)) {
          return { status: "active", state: { ...state, pointerId: null }, textInput: { value: state.text } };
        }
        if (event.phase === "textinput") {
          const next = { ...state, text: event.text.value };
          return {
            status: "active",
            state: next,
            draftParams: { targetVisualizerId: bindings.target, anchor: state.anchor, text: event.text.value },
            textInput: { value: event.text.value },
          };
        }
        if (event.phase === "textcommit") {
          if (!state.text.length) return { status: "cancel" };
          return {
            status: "commit",
            commitParams: { targetVisualizerId: bindings.target, anchor: state.anchor, text: state.text },
          };
        }
        return null;
      },
      hitTest({ params, event, project }) {
        const anchor = project(drawingScenePoint(params.anchor));
        if (!anchor || !finiteNumber(anchor.x) || !finiteNumber(anchor.y)) return null;
        const width = String(params.text || "").length * Number(params.fontSize || 14) * 0.65;
        const height = Number(params.fontSize || 14) * 1.4;
        return event.canvas.x >= anchor.x - 5 && event.canvas.x <= anchor.x + width + 5
          && event.canvas.y >= anchor.y - height && event.canvas.y <= anchor.y + 5 ? {} : null;
      },
      editStart({ params, event }) {
        if (event.phase !== "pointerdown") return null;
        const anchor = drawingEventPoint(event);
        if (!anchor) return null;
        return {
          status: "active", state: { pointerId: event.pointer.pointerId },
          draftParams: { ...params, anchor },
        };
      },
      editReduce({ params, state, event }) {
        if (event.phase === "pointercancel") return { status: "cancel" };
        if (!samePointer(state, event)) return null;
        const anchor = drawingEventPoint(event);
        if (!anchor) return null;
        const next = { ...params, anchor };
        if (event.phase === "pointermove") return { status: "active", state, draftParams: next };
        return event.phase === "pointerup" ? { status: "commit", commitParams: next } : null;
      },
    },
    prepare({ params }) {
      if (!params.anchor || !validChartTime(chartTime(params.anchor.time)) || !finiteNumber(params.anchor.price)
          || typeof params.text !== "string" || !finiteNumber(params.fontSize) || params.fontSize <= 0) {
        throw runtimeError("invalid-drawing", "Text requires an explicit anchor, text, and positive font size.");
      }
      return { anchor: drawingScenePoint(params.anchor), text: params.text };
    },
    draw({ params, prepared, host }) {
      host.attachCanvasScene("target", {
        apiVersion: 1,
        nodes: [{
          kind: "text", anchor: prepared.anchor, text: prepared.text,
          style: { color: params.color, font: `${params.fontSize}px sans-serif` },
        }],
      });
    },
  });

  function safeHostValue(value, label) {
    try {
      return structuredClone(value);
    } catch {
      throw runtimeError("invalid-host-argument", `${label} must contain structured-cloneable data only.`);
    }
  }

  function resolveRequiredResources(plan, resourcesByVisualizerId) {
    const resolved = new Map();
    for (const requirement of plan.capabilities.requires) {
      const targetId = plan.params[requirement.bindingParam];
      const targetResources = resourcesByVisualizerId.get(targetId);
      if (!targetResources) {
        throw runtimeError(
          "missing-overlay-target",
          `Capability '${requirement.name}' target Visualizer '${String(targetId || "")}' is unavailable.`,
        );
      }
      const candidates = [...targetResources.values()].filter((resource) => resource.kind === requirement.kind);
      if (candidates.length !== 1) {
        throw runtimeError(
          candidates.length ? "ambiguous-capability-target" : "missing-overlay-target",
          `Capability '${requirement.name}' target '${targetId}' must provide exactly one '${requirement.kind}' resource.`,
        );
      }
      const resource = candidates[0];
      for (const [attribute, consumerParameter] of Object.entries(requirement.matches)) {
        if (!hasOwn(resource.attributes, attribute)
            || !jsonValuesEqual(resource.attributes[attribute], plan.params[consumerParameter])) {
          throw runtimeError(
            "capability-attribute-mismatch",
            `Capability '${requirement.name}' attribute '${attribute}' does not match its explicit target.`,
          );
        }
      }
      resolved.set(requirement.name, resource);
    }
    return resolved;
  }

  function sameResolvedResources(left, right) {
    if (left.size !== right.size) return false;
    for (const [name, resource] of left) {
      if (right.get(name) !== resource) return false;
    }
    return true;
  }

  function onceCleanup(cleanup) {
    let complete = false;
    return () => {
      if (complete) return;
      cleanup();
      complete = true;
    };
  }

  function drawVisualizerPlan(plan, internals, requiredResources = null) {
    const {
      chart, library, resourcesByVisualizerId, drawingPreviewsById,
      mountCleanupsByVisualizerId, diagnostics, cleanups,
    } = internals;
    const resolvedResources = requiredResources || resolveRequiredResources(plan, resourcesByVisualizerId);
    const providedByName = new Map(plan.capabilities.provides.map((descriptor) => [descriptor.name, descriptor]));
    const createdResources = new Map();
    const rollback = [];
    const drawingArtifacts = [];
    const requireResource = (slot) => {
      if (typeof slot !== "string" || !slot || !resolvedResources.has(slot)) {
        throw runtimeError("host-capability-denied", `Required resource slot '${String(slot || "")}' is not authorized.`);
      }
      return resolvedResources.get(slot);
    };
    const reportDiagnostic = (message) => {
      diagnostics.push(planDiagnostic(plan.instance, runtimeError("renderer-diagnostic", String(message || "Renderer diagnostic"))));
    };
    const host = Object.freeze({
      createSeries(slot, family, options, points) {
        const descriptor = providedByName.get(slot);
        if (!descriptor) throw runtimeError("host-capability-denied", `Provided resource slot '${String(slot || "")}' is not declared.`);
        if (createdResources.has(slot)) throw runtimeError("renderer-series-count", `Provided resource slot '${slot}' was created more than once.`);
        if (!["candlestick", "line", "histogram"].includes(family)) throw runtimeError("unknown-series-family", `Unknown series family '${family}'.`);
        const safeOptions = safeHostValue(options || {}, "Series options");
        const safePoints = safeHostValue(points || [], "Series data");
        const attributes = capabilityAttributeValues(descriptor, plan.params);
        if (hasOwn(attributes, "price-scale")) safeOptions.priceScaleId = attributes["price-scale"];
        const series = addChartSeries(library, chart, family, safeOptions);
        if (typeof chart.removeSeries !== "function") {
          throw runtimeError("host-capability-missing", "The chart cannot detach an explicitly created series.");
        }
        rollback.push(() => chart.removeSeries(series));
        series.setData(safePoints);
        createdResources.set(slot, Object.freeze({
          visualizerId: plan.instance.id,
          slot,
          kind: descriptor.kind,
          attributes,
          series,
        }));
      },
      attachMarkers(slot, markers) {
        const target = requireResource(slot).series;
        const safeMarkers = safeHostValue(markers || [], "Markers");
        if (typeof library.createSeriesMarkers !== "function") {
          throw runtimeError("host-capability-missing", "The chart library cannot attach markers.");
        }
        const primitive = library.createSeriesMarkers(target, safeMarkers);
        if (typeof primitive?.detach !== "function") {
          throw runtimeError("host-capability-missing", "The marker primitive cannot be detached.");
        }
        rollback.push(() => primitive.detach());
      },
      attachPriceLine(slot, options) {
        const target = requireResource(slot).series;
        if (typeof target.createPriceLine !== "function" || typeof target.removePriceLine !== "function") {
          throw runtimeError("host-capability-missing", "The explicit resource cannot host a detachable price line.");
        }
        let accepted = safeHostValue(options || {}, "Price Line options");
        let displayed = accepted;
        const priceLine = target.createPriceLine(accepted);
        if (typeof priceLine?.applyOptions !== "function") {
          target.removePriceLine(priceLine);
          throw runtimeError("host-capability-missing", "The price line cannot be transactionally updated.");
        }
        let detached = false;
        const replace = (next) => {
          if (detached) throw runtimeError("price-line-detached", "Price Line is detached.");
          priceLine.applyOptions(next);
          displayed = next;
        };
        const artifact = Object.freeze({
          kind: "price-line",
          slot,
          stage(nextOptions) {
            const next = safeHostValue(nextOptions || {}, "Price Line preview options");
            const previous = displayed;
            let committed = false;
            return Object.freeze({
              commit() {
                committed = true;
                try {
                  replace(next);
                } catch (error) {
                  let rollbackError = null;
                  try { replace(previous); } catch (failure) { rollbackError = failure; }
                  committed = false;
                  if (rollbackError) {
                    throw runtimeError(
                      "drawing-preview-rollback-failed",
                      rollbackError?.message || "Price Line preview rollback failed.",
                    );
                  }
                  throw error;
                }
              },
              rollback() {
                if (!committed) return;
                replace(previous);
                committed = false;
              },
            });
          },
          accept() { accepted = displayed; },
          restore() { replace(accepted); },
          detach() {
            if (detached) return;
            target.removePriceLine(priceLine);
            detached = true;
          },
        });
        rollback.push(() => artifact.detach());
        drawingArtifacts.push(artifact);
      },
      attachCanvasScene(slot, scene) {
        const target = requireResource(slot).series;
        const artifact = createCanvasSceneArtifact(chart, target, scene, reportDiagnostic);
        rollback.push(() => artifact.detach());
        drawingArtifacts.push(Object.freeze({ ...artifact, slot }));
      },
      lineStyle(value) { return lineStyle(library, value); },
      reportDiagnostic,
    });
    const payload = Object.freeze({
      instanceId: plan.instance.id,
      displayLabel: plan.displayLabel,
      callback: plan.instance.callback,
      params: plan.params,
      inputs: plan.inputs,
      prepared: plan.prepared,
      host,
    });
    try {
      plan.renderer.draw(payload);
      if (createdResources.size !== providedByName.size) {
        throw runtimeError("renderer-produced-no-resource", "Renderer did not create every declared provided resource.");
      }
      if (createdResources.size) resourcesByVisualizerId.set(plan.instance.id, createdResources);
      if (plan.renderer.interaction && drawingArtifacts.length && drawingPreviewsById) {
        let originalParams = frozenClone(plan.params);
        let displayedParams = originalParams;
        const renderPreview = (paramsValue) => {
          const params = deepFreeze(safeHostValue(paramsValue, "Drawing preview parameters"));
          const previewPlan = { ...plan, params };
          const nextResources = resolveRequiredResources(previewPlan, resourcesByVisualizerId);
          if (!sameResolvedResources(resolvedResources, nextResources)) {
            throw runtimeError("host-capability-denied", "A drawing preview cannot change an authorized resource binding.");
          }
          const preparedValue = typeof plan.renderer.prepare === "function"
            ? plan.renderer.prepare(Object.freeze({
              instanceId: plan.instance.id,
              callback: plan.instance.callback,
              params,
              inputs: plan.inputs,
            }))
            : null;
          const prepared = frozenClone(preparedValue);
          let artifactIndex = 0;
          const staged = [];
          const nextArtifact = (kind, slot) => {
            const artifact = drawingArtifacts[artifactIndex];
            artifactIndex += 1;
            if (!artifact || artifact.kind !== kind || artifact.slot !== slot) {
              throw runtimeError("drawing-preview-shape", "Drawing preview primitives differ from the committed drawing.");
            }
            return artifact;
          };
          const previewHost = Object.freeze({
            attachPriceLine(slot, options) { staged.push(nextArtifact("price-line", slot).stage(options)); },
            attachCanvasScene(slot, scene) { staged.push(nextArtifact("canvas-scene", slot).stage(scene)); },
            lineStyle(value) { return lineStyle(library, value); },
            reportDiagnostic(message) {
              throw runtimeError("renderer-diagnostic", String(message || "Drawing preview diagnostic"));
            },
          });
          plan.renderer.draw(Object.freeze({
            instanceId: plan.instance.id,
            displayLabel: plan.displayLabel,
            callback: plan.instance.callback,
            params,
            inputs: plan.inputs,
            prepared,
            host: previewHost,
          }));
          if (artifactIndex !== drawingArtifacts.length) {
            throw runtimeError("drawing-preview-shape", "Drawing preview omitted a committed primitive.");
          }
          const committed = [];
          try {
            for (const transaction of staged) {
              transaction.commit();
              committed.push(transaction);
            }
          } catch (error) {
            let rollbackError = null;
            committed.reverse().forEach((transaction) => {
              try { transaction.rollback(); } catch (failure) {
                if (!rollbackError) rollbackError = failure;
              }
            });
            if (rollbackError || error?.code === "drawing-preview-rollback-failed") {
              throw runtimeError(
                "drawing-preview-rollback-failed",
                rollbackError?.message || error?.message || "Drawing preview rollback failed.",
              );
            }
            throw error;
          }
          return params;
        };
        drawingPreviewsById.set(plan.instance.id, Object.freeze({
          apply(params) {
            displayedParams = renderPreview(params);
          },
          accept() {
            drawingArtifacts.forEach((artifact) => artifact.accept());
            originalParams = displayedParams;
          },
          restore() {
            if (!jsonValuesEqual(displayedParams, originalParams)) renderPreview(originalParams);
            displayedParams = originalParams;
          },
        }));
      }
      const ownedCleanups = rollback.map(onceCleanup);
      if (createdResources.size) {
        ownedCleanups.push(onceCleanup(() => {
          if (resourcesByVisualizerId.get(plan.instance.id) === createdResources) {
            resourcesByVisualizerId.delete(plan.instance.id);
          }
        }));
      }
      cleanups.push(...ownedCleanups);
      mountCleanupsByVisualizerId?.set(plan.instance.id, ownedCleanups);
      return true;
    } catch (error) {
      if (createdResources.size) resourcesByVisualizerId.delete(plan.instance.id);
      let rollbackError = null;
      rollback.reverse().forEach((cleanup) => {
        try { cleanup(); } catch (failure) { if (!rollbackError) rollbackError = failure; }
      });
      diagnostics.push(planDiagnostic(
        plan.instance,
        rollbackError
          ? runtimeError(
            "renderer-rollback-failed",
            rollbackError?.message || "Renderer rollback failed.",
          )
          : error,
        "renderer-draw-error",
      ));
      return false;
    }
  }

  function drawingDistanceToSegment(point, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (!dx && !dy) return Math.hypot(point.x - start.x, point.y - start.y);
    const ratio = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(point.x - (start.x + ratio * dx), point.y - (start.y + ratio * dy));
  }

  function createInteractionController(chart, pane, plansById, internals) {
    const {
      resourcesByVisualizerId, drawingPreviewsById, mountCleanupsByVisualizerId,
    } = internals;
    const listeners = new Set();
    const container = chartContainers.get(chart) || null;
    const pointerAvailable = !!container?.addEventListener && !!chart?.timeScale;
    const usedIds = new Set((pane?.visualizers || []).map((instance) => instance?.id).filter(Boolean));
    const toolEntries = [];
    const interactionFields = [
      "toolId", "label", "consumes", "coordinateResource",
      "start", "reduce", "hitTest", "editStart", "editReduce",
    ];
    for (const rendererId of Object.keys(hostRenderers)) {
      try {
        const renderer = hostRenderers[rendererId];
        const interaction = renderer?.interaction;
        if (!interaction) continue;
        requireExactHostFields(interaction, interactionFields, `Renderer '${rendererId}' interaction`);
        const definition = visualizerDefinitionById(rendererId);
        if (!definition || rendererIdForDefinition(definition) !== rendererId
            || renderer.apiVersion !== definition.renderer.apiVersion) continue;
        const capabilities = definitionCapabilities(definition);
        if (typeof interaction.toolId !== "string" || !interaction.toolId.trim()
            || typeof interaction.label !== "string" || !interaction.label.trim()
            || !Array.isArray(interaction.consumes)
            || interaction.consumes.some((value) => typeof value !== "string" || !value.trim())
            || !jsonValuesEqual([...interaction.consumes].sort(), [...capabilities.interactions].sort())
            || typeof interaction.coordinateResource !== "string" || !interaction.coordinateResource.trim()
            || !capabilities.requires.some((requirement) => requirement.name === interaction.coordinateResource)
            || ["start", "reduce", "hitTest", "editStart", "editReduce"].some((name) => typeof interaction[name] !== "function")) {
          throw runtimeError("invalid-interaction-contract", `Renderer '${rendererId}' interaction contract is invalid.`);
        }
        if (toolEntries.some((entry) => entry.interaction.toolId === interaction.toolId)) {
          throw runtimeError("invalid-interaction-contract", `Drawing tool '${interaction.toolId}' is duplicated.`);
        }
        toolEntries.push({ definition, renderer, interaction, capabilities });
      } catch (error) {
        internals.diagnostics.push(planDiagnostic(
          { id: "", callback: rendererId }, error, "invalid-interaction-contract",
        ));
      }
    }
    const toolById = new Map(toolEntries.map((entry) => [entry.interaction.toolId, entry]));
    const entryByCallback = new Map(toolEntries.map((entry) => [entry.definition.id, entry]));
    const liveRecordsById = new Map();
    let liveOrder = [];
    for (const instance of pane?.visualizers || []) {
      const plan = plansById.get(instance?.id);
      if (!plan?.renderer?.interaction || !entryByCallback.has(instance.callback)) continue;
      liveRecordsById.set(instance.id, {
        instance: effectivePlanInstance(plan),
        plan,
        preview: drawingPreviewsById.get(instance.id) || null,
        cleanups: mountCleanupsByVisualizerId.get(instance.id) || [],
      });
      liveOrder.push(instance.id);
    }
    let activeTool = "select";
    let activeBindings = Object.freeze(Object.create(null));
    let selectedId = null;
    let session = null;
    let selectionGesture = null;
    let disposed = false;
    let reconciliationFailedClosed = false;
    const failedClosedRecords = [];
    const capturedPointers = new Set();
    const pointerListenerOptions = { capture: true, passive: false };

    const emit = (event) => {
      const safe = frozenClone(event);
      listeners.forEach((listener) => {
        try { listener(safe); } catch { /* listener isolation */ }
      });
    };
    const diagnostic = (message) => emit({ type: "diagnostic", message: String(message || "Drawing interaction failed.") });
    const consumePointer = (event) => {
      event?.preventDefault?.();
      event?.stopPropagation?.();
    };
    const candidateForRequirement = (visualizerId, requirement) => {
      const resources = resourcesByVisualizerId.get(visualizerId);
      if (!resources) return null;
      const matches = [...resources.values()].filter((resource) => resource.kind === requirement.kind);
      return matches.length === 1 ? matches[0] : null;
    };
    const candidateList = (requirement) => [...resourcesByVisualizerId.keys()].flatMap((visualizerId) => {
      if (!candidateForRequirement(visualizerId, requirement)) return [];
      const plan = plansById.get(visualizerId);
      return [{ visualizerId, label: plan?.displayLabel || "Data Display" }];
    });
    const listTools = () => frozenClone({
      tools: pointerAvailable ? [
        { id: "select", label: "Select", bindings: [] },
        ...toolEntries.map((entry) => ({
          id: entry.interaction.toolId,
          label: entry.interaction.label,
          bindings: entry.capabilities.requires.map((requirement) => ({
            name: requirement.name,
            label: requirement.name,
            kind: requirement.kind,
            candidates: candidateList(requirement),
          })),
        })),
      ] : [],
    });
    const drawingInstance = (definition, params, existingId = "") => {
      const withDefaults = effectiveInstanceParams(definition, { params });
      let id = existingId;
      if (!id) {
        do {
          const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 9)}`;
          id = `${definition.id}.${suffix}`;
        } while (usedIds.has(id));
      }
      usedIds.add(id);
      return { id, callback: definition.id, params: structuredClone(withDefaults) };
    };
    const resolvedBindings = (entry, bindings) => {
      if (!bindings || typeof bindings !== "object" || Array.isArray(bindings)) {
        throw runtimeError("invalid-interaction-binding", "Drawing bindings must be an object.");
      }
      const names = Object.keys(bindings);
      const requirements = entry.capabilities.requires;
      if (names.length !== requirements.length || names.some((name) => !requirements.some((item) => item.name === name))) {
        throw runtimeError("invalid-interaction-binding", "Drawing bindings do not match the tool contract.");
      }
      const result = Object.create(null);
      requirements.forEach((requirement) => {
        const visualizerId = bindings[requirement.name];
        if (typeof visualizerId !== "string" || !candidateForRequirement(visualizerId, requirement)) {
          throw runtimeError("invalid-interaction-binding", `Binding '${requirement.name}' has no compatible explicit resource.`);
        }
        result[requirement.name] = visualizerId;
      });
      return deepFreeze(result);
    };
    const interactionResource = (entry, bindings, plan = null) => {
      const requirement = entry.capabilities.requires.find((item) => item.name === entry.interaction.coordinateResource);
      if (!requirement) throw runtimeError("invalid-interaction-contract", "Interaction coordinate resource is undeclared.");
      if (plan) return resolveRequiredResources(plan, resourcesByVisualizerId).get(requirement.name);
      const visualizerId = bindings[requirement.name];
      const resource = candidateForRequirement(visualizerId, requirement);
      if (!resource) throw runtimeError("invalid-interaction-binding", "Interaction coordinate resource is unavailable.");
      return resource;
    };
    const numericPointerValue = (value) => finiteNumber(value) ? value : null;
    const pointerEnvelope = (event, phase, resource) => {
      if (!container?.getBoundingClientRect) throw runtimeError("host-capability-missing", "Chart pointer bounds are unavailable.");
      const bounds = container.getBoundingClientRect();
      const x = Number(event.clientX) - Number(bounds.left);
      const y = Number(event.clientY) - Number(bounds.top);
      if (!finiteNumber(x) || !finiteNumber(y)) throw runtimeError("invalid-pointer-event", "Pointer coordinates must be finite.");
      const rawX = chart.timeScale().coordinateToTime(x);
      const rawY = resource.series.coordinateToPrice(y);
      const domain = validChartTime(chartTime(rawX)) && finiteNumber(rawY)
        ? Object.freeze({ x: structuredClone(rawX), y: rawY })
        : null;
      return deepFreeze({
        kind: "pointer",
        phase,
        canvas: { x, y },
        domain,
        pointer: {
          pointerId: numericPointerValue(event.pointerId),
          pointerType: typeof event.pointerType === "string" ? event.pointerType : "",
          isPrimary: !!event.isPrimary,
          button: numericPointerValue(event.button),
          buttons: numericPointerValue(event.buttons),
          width: numericPointerValue(event.width),
          height: numericPointerValue(event.height),
          pressure: numericPointerValue(event.pressure),
          tangentialPressure: numericPointerValue(event.tangentialPressure),
          tiltX: numericPointerValue(event.tiltX),
          tiltY: numericPointerValue(event.tiltY),
          twist: numericPointerValue(event.twist),
          altitudeAngle: numericPointerValue(event.altitudeAngle),
          azimuthAngle: numericPointerValue(event.azimuthAngle),
          timeStamp: numericPointerValue(event.timeStamp),
        },
        modifiers: {
          alt: !!event.altKey,
          ctrl: !!event.ctrlKey,
          meta: !!event.metaKey,
          shift: !!event.shiftKey,
        },
      });
    };
    const projectForResource = (resource) => (point) => {
      if (!point || !validChartTime(chartTime(point.x)) || !finiteNumber(point.y)) return null;
      const x = chart.timeScale().timeToCoordinate(chartTime(point.x));
      const y = resource.series.priceToCoordinate(point.y);
      return finiteNumber(x) && finiteNumber(y) ? Object.freeze({ x, y }) : null;
    };
    const normalizeEffect = (value) => {
      if (value === null || value === undefined) return null;
      const fields = ["status", "state", "draftParams", "commitParams", "textInput"];
      if (!value || typeof value !== "object" || Array.isArray(value)
          || Object.keys(value).some((name) => !fields.includes(name))) {
        throw runtimeError("invalid-interaction-effect", "Interaction returned an unknown effect shape.");
      }
      if (!["active", "commit", "cancel"].includes(value.status)) {
        throw runtimeError("invalid-interaction-effect", "Interaction effect status is invalid.");
      }
      if (value.status === "active" && !hasOwn(value, "state")) {
        throw runtimeError("invalid-interaction-effect", "An active interaction effect requires opaque state.");
      }
      if (value.status === "commit" && !hasOwn(value, "commitParams")) {
        throw runtimeError("invalid-interaction-effect", "A commit interaction effect requires complete parameters.");
      }
      if (value.status === "cancel" && Object.keys(value).length !== 1) {
        throw runtimeError("invalid-interaction-effect", "A cancel interaction effect cannot contain payload data.");
      }
      let textInput;
      if (hasOwn(value, "textInput")) {
        requireExactHostFields(value.textInput, ["value"], "Text input request");
        if (typeof value.textInput.value !== "string") {
          throw runtimeError("invalid-interaction-effect", "Text input request value must be a string.");
        }
        textInput = Object.freeze({ value: value.textInput.value });
      }
      return Object.freeze({
        status: value.status,
        ...(hasOwn(value, "state") ? { state: frozenClone(safeHostValue(value.state, "Interaction state")) } : {}),
        ...(hasOwn(value, "draftParams") ? { draftParams: frozenClone(safeHostValue(value.draftParams, "Draft parameters")) } : {}),
        ...(hasOwn(value, "commitParams") ? { commitParams: frozenClone(safeHostValue(value.commitParams, "Committed parameters")) } : {}),
        ...(textInput ? { textInput } : {}),
      });
    };

    const releaseCapturedPointer = (pointerId) => {
      if (pointerId === null || !capturedPointers.has(pointerId)) return;
      capturedPointers.delete(pointerId);
      try { container.releasePointerCapture?.(pointerId); } catch { /* capture lifecycle is browser-owned */ }
    };
    const releaseAllPointers = () => [...capturedPointers].forEach(releaseCapturedPointer);
    const closeTextInput = () => {
      const editor = session?.textEditor;
      if (!editor) return;
      editor.dispose();
      session.textEditor = null;
    };
    const runCleanups = (cleanups) => {
      let firstError = null;
      (cleanups || []).slice().reverse().forEach((cleanup) => {
        try { cleanup(); } catch (error) { if (!firstError) firstError = error; }
      });
      return firstError;
    };
    const cleanupDraft = (value) => {
      const cleanups = value?.draftCleanups || [];
      const error = runCleanups(cleanups);
      if (!error) value.draftCleanups = [];
      if (error) throw error;
    };
    const cleanupRecord = (record) => {
      const error = runCleanups(record?.cleanups || []);
      if (error) throw error;
    };
    const prepareLivePlan = (instance) => {
      const entry = entryByCallback.get(instance?.callback);
      if (!entry) {
        throw runtimeError(
          "presentation-instance-unavailable",
          "The Visualizer is outside this interaction controller's declared capabilities.",
        );
      }
      const plan = prepareVisualizerPlan(
        internals.result || { dataKeys: {}, cycles: [] },
        internals.spec || {},
        instance,
      );
      if (plan.renderer !== entry.renderer || !plan.renderer.interaction) {
        throw runtimeError("invalid-interaction-contract", "The Visualizer interaction contract changed during presentation reconciliation.");
      }
      plan.displayLabel = visualizerDisplayBase(plan.instance, plan.definition);
      resolveRequiredResources(plan, resourcesByVisualizerId);
      return plan;
    };
    const mountLiveRecord = (instance, plan) => {
      const localPreviews = new Map();
      const localCleanups = [];
      const localMountCleanups = new Map();
      const localDiagnostics = [];
      const localInternals = {
        ...internals,
        diagnostics: localDiagnostics,
        cleanups: localCleanups,
        drawingPreviewsById: localPreviews,
        mountCleanupsByVisualizerId: localMountCleanups,
      };
      const required = resolveRequiredResources(plan, resourcesByVisualizerId);
      if (!drawVisualizerPlan(plan, localInternals, required)) {
        const failure = localDiagnostics[localDiagnostics.length - 1];
        throw runtimeError(
          failure?.code || "presentation-reconcile-failed",
          failure?.internalMessage || failure?.message || "The Visualizer presentation could not be mounted.",
        );
      }
      const preview = localPreviews.get(instance.id);
      if (!preview) {
        const rollbackError = runCleanups(localCleanups);
        if (rollbackError) {
          throw runtimeError(
            "renderer-rollback-failed",
            rollbackError?.message || "Interactive Visualizer mount rollback failed.",
          );
        }
        throw runtimeError("drawing-preview-missing", "An interactive Visualizer did not provide an updateable presentation artifact.");
      }
      return {
        instance: effectivePlanInstance(plan),
        plan,
        preview,
        cleanups: localMountCleanups.get(instance.id) || localCleanups,
      };
    };
    const managedPresentation = (visualizers) => {
      if (!Array.isArray(visualizers)) {
        throw runtimeError("invalid-presentation-reconcile", "Visualizer presentation must be an array.");
      }
      const duplicateIds = duplicatePaneVisualizerIds({ visualizers });
      const order = [];
      const records = new Map();
      for (const instance of visualizers) {
        requireUniquePaneVisualizerInstance(instance, duplicateIds);
        if (instance?.visible === false || !entryByCallback.has(instance?.callback)) continue;
        const plan = prepareLivePlan(instance);
        order.push(instance.id);
        records.set(instance.id, { instance: effectivePlanInstance(plan), plan });
      }
      return { order, records };
    };
    const cancelSession = () => {
      selectionGesture = null;
      if (!session) {
        releaseAllPointers();
        container?.classList?.remove?.("chart-interaction-active");
        return true;
      }
      const cancelled = session;
      closeTextInput();
      releaseAllPointers();
      let rollbackError = null;
      try { cancelled.preview?.restore?.(); } catch (error) { rollbackError = error; }
      try { cleanupDraft(cancelled); } catch (error) { if (!rollbackError) rollbackError = error; }
      session = null;
      container?.classList?.remove?.("chart-interaction-active");
      if (rollbackError) {
        if (cancelled.draftCleanups?.length) {
          failedClosedRecords.push({ cleanups: cancelled.draftCleanups });
          cancelled.draftCleanups = [];
        }
        reconciliationFailedClosed = true;
        diagnostic(rollbackError?.message || "Drawing preview rollback failed.");
        return false;
      }
      return true;
    };
    const mountCreationPreview = (value, instance) => {
      const plan = prepareLivePlan(instance);
      const localPreviews = new Map();
      const localCleanups = [];
      const localMountCleanups = new Map();
      const localDiagnostics = [];
      const localInternals = {
        ...internals,
        diagnostics: localDiagnostics,
        cleanups: localCleanups,
        drawingPreviewsById: localPreviews,
        mountCleanupsByVisualizerId: localMountCleanups,
      };
      const required = resolveRequiredResources(plan, resourcesByVisualizerId);
      if (!drawVisualizerPlan(plan, localInternals, required)) {
        const failure = localDiagnostics[localDiagnostics.length - 1];
        if (failure?.code === "renderer-rollback-failed") reconciliationFailedClosed = true;
        throw runtimeError(
          failure?.code || "drawing-preview-failed",
          failure?.message || "Drawing creation preview could not be mounted.",
        );
      }
      const preview = localPreviews.get(instance.id);
      if (!preview) {
        const rollbackError = runCleanups(localCleanups);
        if (rollbackError) {
          reconciliationFailedClosed = true;
          throw runtimeError(
            "renderer-rollback-failed",
            rollbackError?.message || "Drawing creation preview rollback failed.",
          );
        }
        throw runtimeError("drawing-preview-missing", "Drawing renderer did not provide an updateable preview artifact.");
      }
      value.preview = preview;
      value.plan = plan;
      value.draftCleanups = localMountCleanups.get(instance.id) || localCleanups;
    };
    const applyDraftParams = (rawParams) => {
      const instance = drawingInstance(session.entry.definition, rawParams, session.instanceId);
      session.instanceId = instance.id;
      if (!session.preview) {
        mountCreationPreview(session, instance);
      } else {
        session.preview.apply(instance.params);
      }
      session.params = instance.params;
      return instance;
    };
    const promoteCommittedSession = (completed, instance) => {
      const plan = prepareLivePlan(instance);
      const effectiveInstance = effectivePlanInstance(plan);
      const id = instance.id;
      const existing = liveRecordsById.get(id);
      if (completed.editing) {
        if (!existing || existing.preview !== completed.preview) {
          throw runtimeError("presentation-instance-missing", "The edited Visualizer is no longer in the live presentation.");
        }
        completed.preview.accept();
        existing.instance = effectiveInstance;
        existing.plan = plan;
        plansById.set(id, plan);
        drawingPreviewsById.set(id, completed.preview);
        return effectiveInstance;
      }
      if (existing) {
        throw runtimeError("duplicate-visualizer-id", "The committed Visualizer identity already exists in the live presentation.");
      }
      completed.preview.accept();
      const cleanups = completed.draftCleanups;
      completed.draftCleanups = [];
      liveRecordsById.set(id, {
        instance: effectiveInstance,
        plan,
        preview: completed.preview,
        cleanups,
      });
      liveOrder.push(id);
      plansById.set(id, plan);
      drawingPreviewsById.set(id, completed.preview);
      mountCleanupsByVisualizerId.set(id, cleanups);
      return effectiveInstance;
    };
    const reconcilePresentation = (visualizers) => {
      if (disposed || reconciliationFailedClosed) {
        throw runtimeError("presentation-reconcile-locked", "The interaction presentation is unavailable.");
      }
      if (!cancelSession()) {
        throw runtimeError("presentation-reconcile-rollback-failed", "The active interaction could not be rolled back.");
      }
      const target = managedPresentation(visualizers);
      const staged = new Map();
      const updates = [];
      const isRollbackFailure = (error) => [
        "drawing-preview-rollback-failed",
        "renderer-rollback-failed",
        "presentation-reconcile-cleanup-failed",
        "presentation-reconcile-rollback-failed",
      ].includes(error?.code);
      const rollbackStaged = () => {
        let firstError = null;
        [...staged.values()].reverse().forEach((record) => {
          try { cleanupRecord(record); } catch (error) {
            failedClosedRecords.push(record);
            if (!firstError) firstError = error;
          }
        });
        return firstError;
      };
      const rollbackUpdates = (applied) => {
        let firstError = null;
        applied.slice().reverse().forEach((update) => {
          try { update.current.preview.apply(update.current.plan.params); } catch (error) {
            if (!firstError) firstError = error;
          }
        });
        return firstError;
      };
      const rollbackFailure = (...errors) => {
        const failure = errors.find(Boolean);
        reconciliationFailedClosed = true;
        return runtimeError(
          "presentation-reconcile-rollback-failed",
          failure?.message || "Visualizer presentation rollback failed.",
        );
      };
      try {
        for (const id of target.order) {
          const next = target.records.get(id);
          const current = liveRecordsById.get(id);
          if (!current) {
            staged.set(id, mountLiveRecord(next.instance, next.plan));
            continue;
          }
          if (jsonValuesEqual(current.instance, next.instance)) continue;
          const currentResources = resolveRequiredResources(current.plan, resourcesByVisualizerId);
          const nextResources = resolveRequiredResources(next.plan, resourcesByVisualizerId);
          const canUpdate = current.instance.callback === next.instance.callback
            && !!current.preview
            && jsonValuesEqual(current.plan.inputs, next.plan.inputs)
            && sameResolvedResources(currentResources, nextResources);
          if (canUpdate) {
            updates.push({ current, next });
            continue;
          }
          if (current.plan.capabilities.provides.length || next.plan.capabilities.provides.length) {
            throw runtimeError(
              "presentation-reconcile-nonatomic",
              "This Visualizer replacement cannot be staged without changing a live provided resource.",
            );
          }
          staged.set(id, mountLiveRecord(next.instance, next.plan));
        }
      } catch (error) {
        const stagedRollbackError = rollbackStaged();
        if (isRollbackFailure(error) || stagedRollbackError) {
          throw rollbackFailure(stagedRollbackError, error);
        }
        throw error;
      }

      const appliedUpdates = [];
      try {
        for (const update of updates) {
          update.current.preview.apply(update.next.plan.params);
          appliedUpdates.push(update);
        }
      } catch (error) {
        const updateRollbackError = rollbackUpdates(appliedUpdates);
        const stagedRollbackError = rollbackStaged();
        if (isRollbackFailure(error) || updateRollbackError || stagedRollbackError) {
          throw rollbackFailure(updateRollbackError, stagedRollbackError, error);
        }
        throw error;
      }

      const obsolete = [...liveRecordsById.entries()].filter(([id, record]) => (
        !target.records.has(id) || staged.has(id) && staged.get(id) !== record
      ));
      try {
        obsolete.forEach(([, record]) => cleanupRecord(record));
      } catch (error) {
        const updateRollbackError = rollbackUpdates(appliedUpdates);
        const stagedRollbackError = rollbackStaged();
        reconciliationFailedClosed = true;
        throw runtimeError(
          "presentation-reconcile-cleanup-failed",
          updateRollbackError?.message || stagedRollbackError?.message || error?.message
            || "The prior Visualizer presentation could not be detached atomically.",
        );
      }

      obsolete.forEach(([id]) => {
        liveRecordsById.delete(id);
        plansById.delete(id);
        drawingPreviewsById.delete(id);
        mountCleanupsByVisualizerId.delete(id);
      });
      for (const update of updates) {
        update.current.preview.accept();
        update.current.instance = update.next.instance;
        update.current.plan = update.next.plan;
        plansById.set(update.next.instance.id, update.next.plan);
      }
      for (const [id, record] of staged) {
        liveRecordsById.set(id, record);
        plansById.set(id, record.plan);
        drawingPreviewsById.set(id, record.preview);
        mountCleanupsByVisualizerId.set(id, record.cleanups);
      }
      liveOrder = target.order.slice();
      liveOrder.forEach((id) => usedIds.add(id));
      if (selectedId && !liveRecordsById.has(selectedId)) {
        selectedId = null;
        emit({ type: "selection", visualizerId: null });
      }
      return true;
    };
    const textEnvelope = (phase, editor, event = null) => deepFreeze({
      kind: "text",
      phase,
      text: {
        value: String(editor.element.value || ""),
        data: typeof event?.data === "string" ? event.data : null,
        inputType: typeof event?.inputType === "string" ? event.inputType : "",
        isComposing: !!(event?.isComposing || editor.composing),
      },
    });
    const dispatchTextEvent = (phase, editor, event) => {
      if (!session || session.textEditor !== editor) return;
      try {
        const effect = normalizeEffect(session.entry.interaction.reduce(Object.freeze({
          bindings: activeBindings,
          state: session.state,
          event: textEnvelope(phase, editor, event),
        })));
        if (effect) handleEffect(effect, null);
      } catch (error) {
        diagnostic(error?.message || "Text interaction failed.");
        cancelSession();
      }
    };
    const openTextInput = (request) => {
      if (!session.entry.interaction.consumes.includes("chart.text-input")) {
        throw runtimeError("host-capability-denied", "This interaction did not declare text input.");
      }
      if (session.textEditor) {
        if (session.textEditor.element.value !== request.value) session.textEditor.element.value = request.value;
        return;
      }
      const documentValue = window.document;
      if (!documentValue?.createElement || typeof container?.appendChild !== "function") {
        throw runtimeError("host-capability-missing", "Chart-scoped text input is unavailable.");
      }
      const element = documentValue.createElement("textarea");
      element.className = "chart-interaction-text-input";
      element.value = request.value;
      element.setAttribute?.("aria-label", "Chart drawing text");
      element.style.left = `${session.lastCanvas?.x || 0}px`;
      element.style.top = `${session.lastCanvas?.y || 0}px`;
      const editor = { element, composing: false, listeners: [], dispose: null };
      const listen = (type, listener) => {
        element.addEventListener(type, listener);
        editor.listeners.push([type, listener]);
      };
      listen("compositionstart", () => { editor.composing = true; });
      listen("compositionend", (event) => {
        editor.composing = false;
        dispatchTextEvent("textinput", editor, event);
      });
      listen("input", (event) => dispatchTextEvent("textinput", editor, event));
      listen("keydown", (event) => {
        event.stopPropagation?.();
        if (event.key === "Escape") {
          event.preventDefault?.();
          dispatchTextEvent("textcancel", editor, event);
        } else if (event.key === "Enter" && !event.shiftKey && !editor.composing) {
          event.preventDefault?.();
          dispatchTextEvent("textcommit", editor, event);
        }
      });
      listen("blur", (event) => dispatchTextEvent("textcancel", editor, event));
      editor.dispose = () => {
        editor.listeners.forEach(([type, listener]) => element.removeEventListener(type, listener));
        editor.listeners = [];
        element.remove?.();
      };
      session.textEditor = editor;
      container.appendChild(element);
      element.focus?.();
    };
    const handleEffect = (effect, sourceEvent) => {
      if (!session) throw runtimeError("interaction-session-missing", "Interaction session is unavailable.");
      if (sourceEvent?.canvas) session.lastCanvas = sourceEvent.canvas;
      if (effect.status === "cancel") {
        cancelSession();
        return;
      }
      if (hasOwn(effect, "draftParams")) applyDraftParams(effect.draftParams);
      session.state = hasOwn(effect, "state") ? effect.state : session.state;
      if (effect.textInput) openTextInput(effect.textInput);
      if (effect.status !== "commit") return;
      const instance = applyDraftParams(effect.commitParams);
      const completed = session;
      const committedInstance = promoteCommittedSession(completed, instance);
      closeTextInput();
      releaseAllPointers();
      session = null;
      container?.classList?.remove?.("chart-interaction-active");
      emit({ type: "commit", instance: committedInstance });
    };
    const startSession = (entry, effect, options) => {
      if (options.editing && !options.preview) {
        throw runtimeError("drawing-preview-missing", "Selected drawing has no isolated preview artifact.");
      }
      session = {
        entry,
        editing: !!options.editing,
        state: null,
        bindings: options.bindings,
        instanceId: options.instanceId || "",
        params: options.params || null,
        preview: options.preview || null,
        plan: options.plan || null,
        draftCleanups: [],
        lastCanvas: options.event.canvas,
        textEditor: null,
      };
      container?.classList?.add?.("chart-interaction-active");
      handleEffect(effect, options.event);
    };
    const hitTest = (rawEvent, phase) => {
      const drawings = liveOrder.map((id) => liveRecordsById.get(id)).filter(Boolean).reverse();
      for (const record of drawings) {
        const { instance, plan } = record;
        const interaction = plan.renderer.interaction;
        try {
          const entry = toolById.get(interaction.toolId);
          if (!entry) continue;
          const resource = interactionResource(entry, null, plan);
          const event = pointerEnvelope(rawEvent, phase, resource);
          const hit = interaction.hitTest(Object.freeze({
            params: plan.params,
            event,
            project: projectForResource(resource),
          }));
          if (hit) return { instance, plan, entry, hit: frozenClone(safeHostValue(hit, "Hit-test result")), event };
        } catch (error) {
          diagnostic(error?.message || "Drawing hit testing failed.");
        }
      }
      return null;
    };
    const dispatchPointer = (rawEvent, phase) => {
      if (disposed || reconciliationFailedClosed) return;
      let consumed = false;
      try {
        if (session) {
          const resource = interactionResource(session.entry, session.bindings, session.editing ? session.plan : null);
          const event = pointerEnvelope(rawEvent, phase, resource);
          const callback = session.editing ? session.entry.interaction.editReduce : session.entry.interaction.reduce;
          const effect = normalizeEffect(callback(Object.freeze({
            bindings: session.bindings,
            params: session.params,
            state: session.state,
            event,
          })));
          if (effect) {
            consumed = true;
            handleEffect(effect, event);
          }
        } else if (selectionGesture) {
          const gesture = selectionGesture;
          const resource = interactionResource(gesture.match.entry, null, gesture.match.plan);
          const event = pointerEnvelope(rawEvent, phase, resource);
          if (event.pointer.pointerId === gesture.pointerId) {
            consumed = true;
            if (phase === "pointercancel") {
              selectionGesture = null;
            } else if (phase === "pointermove" || phase === "pointerup") {
              const crossed = drawingDragThresholdCrossed(gesture.startCanvas, event.canvas);
              if (crossed && !gesture.editUnavailable) {
                const effect = normalizeEffect(gesture.match.entry.interaction.editStart(Object.freeze({
                  params: gesture.match.plan.params,
                  hit: gesture.match.hit,
                  event: gesture.match.event,
                })));
                if (effect) {
                  selectionGesture = null;
                  startSession(gesture.match.entry, effect, {
                    editing: true,
                    bindings: Object.freeze(Object.fromEntries(
                      gesture.match.entry.capabilities.requires.map((requirement) => (
                        [requirement.name, gesture.match.plan.params[requirement.bindingParam]]
                      )),
                    )),
                    instanceId: gesture.match.instance.id,
                    params: structuredClone(gesture.match.plan.params),
                    preview: drawingPreviewsById.get(gesture.match.instance.id),
                    plan: gesture.match.plan,
                    event: gesture.match.event,
                  });
                  if (session) {
                    const next = normalizeEffect(session.entry.interaction.editReduce(Object.freeze({
                      bindings: session.bindings,
                      params: session.params,
                      state: session.state,
                      event,
                    })));
                    if (next) handleEffect(next, event);
                  }
                } else if (phase === "pointerup") {
                  selectionGesture = null;
                } else {
                  selectionGesture = { ...gesture, editUnavailable: true };
                }
              } else if (phase === "pointerup") {
                selectionGesture = null;
              }
            }
          }
        } else if (activeTool === "select") {
          const match = hitTest(rawEvent, phase);
          if (phase === "pointerdown") {
            selectedId = match?.instance?.id || null;
            emit({ type: "selection", visualizerId: selectedId });
          }
          if (match && phase === "pointerdown") {
            consumed = true;
            selectionGesture = {
              match,
              pointerId: match.event.pointer.pointerId,
              startCanvas: match.event.canvas,
              editUnavailable: false,
            };
          }
        } else {
          const entry = toolById.get(activeTool);
          if (!entry) return;
          const resource = interactionResource(entry, activeBindings);
          const event = pointerEnvelope(rawEvent, phase, resource);
          const effect = normalizeEffect(entry.interaction.start(Object.freeze({ bindings: activeBindings, event })));
          if (effect) {
            consumed = true;
            startSession(entry, effect, { editing: false, bindings: activeBindings, event });
          }
        }
      } catch (error) {
        diagnostic(error?.message || "Drawing interaction failed.");
        cancelSession();
        consumed = true;
      }
      const pointerId = numericPointerValue(rawEvent.pointerId);
      if (consumed) consumePointer(rawEvent);
      if (consumed && phase === "pointerdown" && pointerId !== null && (session || selectionGesture)) {
        container.setPointerCapture?.(pointerId);
        capturedPointers.add(pointerId);
      }
      if (phase === "pointerup" || phase === "pointercancel") releaseCapturedPointer(pointerId);
    };
    const onPointerDown = (event) => dispatchPointer(event, "pointerdown");
    const onPointerMove = (event) => dispatchPointer(event, "pointermove");
    const onPointerUp = (event) => dispatchPointer(event, "pointerup");
    const onPointerCancel = (event) => dispatchPointer(event, "pointercancel");
    const onLostPointerCapture = (event) => {
      const pointerId = numericPointerValue(event.pointerId);
      if (!capturedPointers.has(pointerId)) return;
      capturedPointers.delete(pointerId);
      dispatchPointer(event, "pointercancel");
      if (session || selectionGesture) cancelSession();
    };
    container?.addEventListener?.("pointerdown", onPointerDown, pointerListenerOptions);
    container?.addEventListener?.("pointermove", onPointerMove, pointerListenerOptions);
    container?.addEventListener?.("pointerup", onPointerUp, pointerListenerOptions);
    container?.addEventListener?.("pointercancel", onPointerCancel, pointerListenerOptions);
    container?.addEventListener?.("lostpointercapture", onLostPointerCapture, pointerListenerOptions);
    return Object.freeze({
      listTools,
      activate(toolId, options = {}) {
        try {
          if (disposed || reconciliationFailedClosed || !listTools().tools.some((tool) => tool.id === toolId)) {
            throw runtimeError("unknown-interaction-tool", `Drawing tool '${toolId}' is unavailable.`);
          }
          if (!cancelSession()) {
            throw runtimeError("presentation-reconcile-locked", "The prior interaction could not be rolled back.");
          }
          if (toolId === "select") {
            activeTool = "select";
            activeBindings = Object.freeze(Object.create(null));
            return true;
          }
          requireExactHostFields(options, ["bindings"], "Drawing activation");
          const entry = toolById.get(toolId);
          activeBindings = resolvedBindings(entry, options.bindings);
          activeTool = toolId;
          return true;
        } catch (error) {
          diagnostic(error?.message || "Drawing activation failed.");
          return false;
        }
      },
      cancel() {
        cancelSession();
        activeTool = "select";
        activeBindings = Object.freeze(Object.create(null));
      },
      deleteSelected() {
        if (disposed || reconciliationFailedClosed || !selectedId) return false;
        const visualizerId = selectedId;
        if (!cancelSession()) return false;
        selectedId = null;
        try {
          reconcilePresentation(liveOrder.filter((id) => id !== visualizerId).map((id) => (
            liveRecordsById.get(id).instance
          )));
        } catch (error) {
          selectedId = visualizerId;
          diagnostic(error?.message || "Drawing deletion failed.");
          return false;
        }
        emit({ type: "delete", visualizerId });
        emit({ type: "selection", visualizerId: null });
        return true;
      },
      reconcilePresentation(request) {
        try {
          requireExactHostFields(request, ["visualizers"], "Visualizer presentation reconciliation");
          return reconcilePresentation(request.visualizers);
        } catch (error) {
          diagnostic(error?.message || "Visualizer presentation reconciliation failed.");
          return false;
        }
      },
      subscribe(listener) {
        if (typeof listener !== "function" || disposed) return () => {};
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      dispose() {
        if (disposed) return;
        disposed = true;
        cancelSession();
        [...liveRecordsById.values()].reverse().forEach((record) => {
          try { cleanupRecord(record); } catch { /* cleanup isolation */ }
        });
        failedClosedRecords.splice(0).reverse().forEach((record) => {
          try { cleanupRecord(record); } catch { /* cleanup isolation */ }
        });
        liveRecordsById.clear();
        liveOrder = [];
        container?.removeEventListener?.("pointerdown", onPointerDown, pointerListenerOptions);
        container?.removeEventListener?.("pointermove", onPointerMove, pointerListenerOptions);
        container?.removeEventListener?.("pointerup", onPointerUp, pointerListenerOptions);
        container?.removeEventListener?.("pointercancel", onPointerCancel, pointerListenerOptions);
        container?.removeEventListener?.("lostpointercapture", onLostPointerCapture, pointerListenerOptions);
        listeners.clear();
      },
    });
  }

  function drawFinancialPane(library, chart, result, pane, spec = {}) {
    const diagnostics = [];
    const cleanups = [];
    const plans = [];
    const duplicateIds = duplicatePaneVisualizerIds(pane);
    for (const instance of pane?.visualizers || []) {
      if (!instance || instance.visible === false) continue;
      try {
        requireUniquePaneVisualizerInstance(instance, duplicateIds);
        const plan = prepareVisualizerPlan(result, spec, instance);
        plans.push(plan);
      } catch (error) {
        diagnostics.push(planDiagnostic(instance, error, "renderer-prepare-error"));
      }
    }
    assignVisualizerDisplayLabels(plans);
    let paneTimeDomainId = null;
    const rejected = new Set();
    for (const plan of plans) {
      const domains = planTimeDomainValues(plan);
      if (!domains.length) continue;
      if (domains.length !== 1 || typeof domains[0] !== "string" || !domains[0].trim()) {
        rejected.add(plan.instance.id);
        diagnostics.push(planDiagnostic(plan.instance, runtimeError(
          "invalid-time-domain-capability",
          "A visible provided resource must expose exactly one non-empty time-domain attribute.",
        )));
        continue;
      }
      if (paneTimeDomainId === null) paneTimeDomainId = domains[0];
      if (domains[0] !== paneTimeDomainId) {
        rejected.add(plan.instance.id);
        diagnostics.push(planDiagnostic(plan.instance, runtimeError(
          "time-domain-mismatch",
          `Provided time-domain '${domains[0]}' cannot share a pane with '${paneTimeDomainId}'.`,
        )));
      }
    }
    const internals = {
      chart,
      library,
      result,
      spec,
      diagnostics,
      cleanups,
      resourcesByVisualizerId: new Map(),
      drawingPreviewsById: new Map(),
      mountCleanupsByVisualizerId: new Map(),
    };
    const activePlansById = new Map();
    for (const plan of plans) {
      if (rejected.has(plan.instance.id) || plan.capabilities.requires.length) continue;
      if (drawVisualizerPlan(plan, internals)) activePlansById.set(plan.instance.id, plan);
    }
    for (const plan of plans) {
      if (rejected.has(plan.instance.id) || !plan.capabilities.requires.length) continue;
      let requiredResources;
      try {
        requiredResources = resolveRequiredResources(plan, internals.resourcesByVisualizerId);
      } catch (error) {
        diagnostics.push(planDiagnostic(plan.instance, runtimeError(
          error?.code || "missing-overlay-target",
          error?.message || "Required Visualizer resources are unavailable.",
        )));
        continue;
      }
      if (drawVisualizerPlan(plan, internals, requiredResources)) activePlansById.set(plan.instance.id, plan);
    }
    const interactionController = createInteractionController(chart, pane, activePlansById, internals);
    // Interaction disposal must run before primitive cleanup so an in-flight
    // preview can restore without recreating an already removed primitive.
    cleanups.unshift(() => interactionController.dispose());
    return Object.freeze({
      diagnostics,
      cleanups,
      interactionController,
      styleNormalizationChanged: false,
    });
  }

  window.TradeChartCore = {
    chartTime,
    chartLayerCatalog,
    createFinancialChart,
    dataKeyDeclarations,
    dataKeyCatalogDiagnostics,
    resolveDataKeyDeclaration,
    expandSchemaPaths,
    normalizeSchema,
    schemasCompatible,
    visualizerSchemasCompatible,
    drawCallbackCatalog,
    drawFinancialPane,
    visualizerCatalog,
    setVisualizerDefinitions,
    setTemporaryModuleDefinitions,
    allocateDistinctVisualizerStyles,
    normalizeVisualizerStyleCollisions,
    discoverySourcePaths,
    visualizerDependencyPlan,
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
    upsertIdentity,
  };
}());
