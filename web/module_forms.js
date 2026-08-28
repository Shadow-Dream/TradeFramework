(function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function humanizeName(value) {
    return String(value || "")
      .replaceAll(".", " ")
      .replaceAll("_", " ")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b\w/g, (char) => char.toUpperCase());
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
  const RANDOM_INSTANCE_PATTERN = /^(?:inst[_-]|(?:module|node|input|output|instance)[_-])[a-f0-9]{16,}$/i;
  const RANDOM_INSTANCE_EMBEDDED_PATTERN = /\b(?:inst[_-]|(?:module|node|input|output|instance)[_-])[a-f0-9]{16,}\b/gi;
  const UUID_EMBEDDED_PATTERN = /\b[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\b/gi;
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

  function opaqueMachineIdentityKind(value) {
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

  function userFacingText(value, fallback = "") {
    const original = String(value ?? "");
    const exactKind = opaqueMachineIdentityKind(original);
    if (exactKind) return exactKind;
    const redacted = original
      .replace(RESOURCE_COMPOSITE_EMBEDDED_PATTERN, (_identity, resourceId) => (
        `${resourceIdentityLabel(resourceId) || "Resource"}${_identity.includes("@") ? " version" : ""}`
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

  function humanStatus(value) {
    const text = String(value || "").trim();
    return text ? humanizeName(userFacingText(text)) : "";
  }

  function resourceDisplayName(record = {}, fallbackKind = "Resource") {
    const source = record && typeof record === "object" ? record : {};
    const declared = [source.displayName, source.name, source.label, source.title]
      .map((value) => String(value || "").trim())
      .find((value) => value && !opaqueMachineIdentityKind(value));
    if (declared) return userFacingText(declared, fallbackKind);
    const kind = humanizeName(source.resourceType || source.type || source.kind || fallbackKind) || "Resource";
    const status = humanStatus(source.status || source.state);
    const version = source.version === undefined || source.version === null || source.version === ""
      ? ""
      : `v${userFacingText(source.version)}`;
    return [kind, status || version].filter(Boolean).join(" · ") || "Resource";
  }

  const PRESENTATION_IDENTITY_KEYS = new Set([
    "id", "itemid", "sourceitemid", "datasetid", "datasetversionid", "pipelineid", "moduleid",
    "instanceid", "workspaceid", "recipeid", "scriptid", "samplerid", "environmentid", "analysisid",
    "backtestid", "jobid", "visualizationid", "visualizerid", "snapshotid", "catalogsnapshotid",
    "requestid", "operationid", "versionkey", "contentdigest", "requestdigest", "digest", "contenthash",
    "snapshothash",
  ]);

  function presentationIdentityKey(key, value) {
    const normalized = String(key || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (normalized === "protocolid" || normalized === "providerid" || normalized === "instrumentid") return false;
    if (PRESENTATION_IDENTITY_KEYS.has(normalized)) {
      return normalized !== "id" || Boolean(opaqueMachineIdentityKind(value));
    }
    if (/(?:digest|hash)$/.test(normalized)) return true;
    if (/(?:ids)$/.test(normalized) && normalized !== "periods") return true;
    return false;
  }

  function presentationJson(value) {
    const visit = (current) => {
      if (Array.isArray(current)) return current.map(visit);
      if (!current || typeof current !== "object") {
        return typeof current === "string" ? userFacingText(current) : current;
      }
      const projected = {};
      let anonymousIndex = 0;
      Object.entries(current).forEach(([key, child]) => {
        if (presentationIdentityKey(key, child)) return;
        let visibleKey = userFacingText(key);
        if (opaqueMachineIdentityKind(key)) visibleKey = `Entry ${++anonymousIndex}`;
        while (Object.prototype.hasOwnProperty.call(projected, visibleKey)) visibleKey = `Entry ${++anonymousIndex}`;
        projected[visibleKey] = visit(child);
      });
      return projected;
    };
    return visit(value);
  }

  const schemaPresentationAliases = new WeakMap();

  function schemaPresentationAliasState(...sources) {
    const reserved = new Set();
    const collect = (current) => {
      if (Array.isArray(current)) {
        current.forEach(collect);
        return;
      }
      if (!current || typeof current !== "object") {
        if (typeof current === "string") reserved.add(current);
        return;
      }
      Object.entries(current).forEach(([key, child]) => {
        reserved.add(key);
        collect(child);
      });
    };
    sources.forEach(collect);
    return { aliases: new Map(), reverse: new Map(), reserved, sequence: 0 };
  }

  function schemaPresentationAlias(state, value) {
    const exact = String(value ?? "");
    if (state.reverse.has(exact)) return state.reverse.get(exact);
    const kind = opaqueMachineIdentityKind(exact) || "Technical reference";
    let alias;
    do {
      state.sequence += 1;
      alias = `${kind} (${state.sequence})`;
    } while (state.reserved.has(alias) || state.aliases.has(alias));
    state.aliases.set(alias, exact);
    state.reverse.set(exact, alias);
    return alias;
  }

  function schemaPresentationValue(value, state, restore = false) {
    const visit = (current) => {
      if (Array.isArray(current)) return current.map(visit);
      if (!current || typeof current !== "object") {
        if (typeof current !== "string") return current;
        if (restore) return state.aliases.get(current) || current;
        return userFacingText(current) === current ? current : schemaPresentationAlias(state, current);
      }
      return Object.fromEntries(Object.entries(current).map(([key, child]) => [
        restore
          ? (state.aliases.get(key) || key)
          : (opaqueMachineIdentityKind(key) ? schemaPresentationAlias(state, key) : key),
        visit(child),
      ]));
    };
    return visit(value);
  }

  function normalizedType(spec = {}) {
    let type = spec.type;
    if (Array.isArray(type)) type = type.find((item) => item && item !== "null") || type[0];
    if (!type && spec.enum) return "string";
    if (!type && spec.items) return "array";
    if (!type && spec.properties) return "object";
    return type || "string";
  }

  function schemaTypeLabel(spec = {}) {
    if (!spec || typeof spec !== "object" || Array.isArray(spec) || !Object.keys(spec).length) return "any";
    if (spec.type !== undefined) {
      const types = Array.isArray(spec.type) ? spec.type : [spec.type];
      return [...new Set(types.filter(Boolean))].join(" | ") || "any";
    }
    if (spec.properties || spec.required || spec.additionalProperties !== undefined) return "object";
    if (spec.items) return "array";
    if (spec.const !== undefined) return spec.const === null ? "null" : typeof spec.const;
    if (Array.isArray(spec.enum) && spec.enum.length) {
      return [...new Set(spec.enum.map((item) => item === null ? "null" : typeof item))].join(" | ");
    }
    for (const keyword of ["anyOf", "oneOf"]) {
      if (Array.isArray(spec[keyword]) && spec[keyword].length) {
        return [...new Set(spec[keyword].flatMap((item) => schemaTypeLabel(item).split(" | ")))].join(" | ");
      }
    }
    if (Array.isArray(spec.allOf) && spec.allOf.length) {
      return [...new Set(spec.allOf.map(schemaTypeLabel))].join(" & ");
    }
    return "any";
  }

  function schemaDefaults(schema = {}) {
    const properties = schema.properties || {};
    return Object.fromEntries(Object.entries(properties)
      .filter(([, spec]) => Object.prototype.hasOwnProperty.call(spec || {}, "default"))
      .map(([name, spec]) => [name, spec.default]));
  }

  function jsonEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function valueType(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    if (Number.isInteger(value)) return "integer";
    return typeof value === "object" ? "object" : typeof value;
  }

  function schemaAcceptsType(value, declared) {
    const actual = valueType(value);
    if (declared === "number") return actual === "number" || actual === "integer";
    return actual === declared;
  }

  function validateSchemaValue(value, spec = {}, path = "Value") {
    if (!spec || typeof spec !== "object" || Array.isArray(spec) || !Object.keys(spec).length) return;
    if (spec.const !== undefined && !jsonEqual(value, spec.const)) {
      throw new Error(`${path} must equal ${JSON.stringify(spec.const)}`);
    }
    if (Array.isArray(spec.enum) && !spec.enum.some((item) => jsonEqual(item, value))) {
      throw new Error(`${path} must be one of the declared values`);
    }
    if (Array.isArray(spec.allOf)) {
      spec.allOf.forEach((branch) => validateSchemaValue(value, branch, path));
    }
    if (Array.isArray(spec.anyOf)) {
      const matches = spec.anyOf.filter((branch) => {
        try { validateSchemaValue(value, branch, path); return true; } catch { return false; }
      }).length;
      if (!matches) throw new Error(`${path} does not match any allowed schema`);
    }
    if (Array.isArray(spec.oneOf)) {
      const matches = spec.oneOf.filter((branch) => {
        try { validateSchemaValue(value, branch, path); return true; } catch { return false; }
      }).length;
      if (matches !== 1) throw new Error(`${path} must match exactly one allowed schema`);
    }

    const declared = spec.type === undefined
      ? []
      : (Array.isArray(spec.type) ? spec.type : [spec.type]);
    if (declared.length && !declared.some((type) => schemaAcceptsType(value, type))) {
      throw new Error(`${path} must be ${declared.join(" or ")}`);
    }

    if (typeof value === "number") {
      if (!Number.isFinite(value)) throw new Error(`${path} must be a finite number`);
      if (spec.minimum !== undefined && value < spec.minimum) throw new Error(`${path} must be at least ${spec.minimum}`);
      if (spec.maximum !== undefined && value > spec.maximum) throw new Error(`${path} must be at most ${spec.maximum}`);
      if (spec.exclusiveMinimum !== undefined && value <= spec.exclusiveMinimum) throw new Error(`${path} must be greater than ${spec.exclusiveMinimum}`);
      if (spec.exclusiveMaximum !== undefined && value >= spec.exclusiveMaximum) throw new Error(`${path} must be less than ${spec.exclusiveMaximum}`);
      if (spec.multipleOf !== undefined) {
        const ratio = value / spec.multipleOf;
        if (!Number.isFinite(ratio) || Math.abs(ratio - Math.round(ratio)) > 1e-9) {
          throw new Error(`${path} must be a multiple of ${spec.multipleOf}`);
        }
      }
    }
    if (typeof value === "string") {
      if (spec.minLength !== undefined && value.length < spec.minLength) throw new Error(`${path} is shorter than ${spec.minLength} characters`);
      if (spec.maxLength !== undefined && value.length > spec.maxLength) throw new Error(`${path} is longer than ${spec.maxLength} characters`);
      if (spec.pattern !== undefined && !(new RegExp(spec.pattern).test(value))) throw new Error(`${path} does not match the required pattern`);
    }
    if (Array.isArray(value)) {
      if (spec.minItems !== undefined && value.length < spec.minItems) throw new Error(`${path} requires at least ${spec.minItems} items`);
      if (spec.maxItems !== undefined && value.length > spec.maxItems) throw new Error(`${path} allows at most ${spec.maxItems} items`);
      if (spec.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) {
        throw new Error(`${path} requires unique items`);
      }
      if (spec.items && typeof spec.items === "object") {
        value.forEach((item, index) => validateSchemaValue(item, spec.items, `${path}[${index}]`));
      }
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const properties = spec.properties || {};
      (spec.required || []).forEach((name) => {
        if (!Object.prototype.hasOwnProperty.call(value, name)) throw new Error(`${path}.${name} is required`);
      });
      Object.entries(value).forEach(([name, child]) => {
        if (properties[name]) validateSchemaValue(child, properties[name], `${path}.${name}`);
        else if (spec.additionalProperties === false) throw new Error(`${path}.${name} is not allowed`);
        else if (spec.additionalProperties && typeof spec.additionalProperties === "object") {
          validateSchemaValue(child, spec.additionalProperties, `${path}.${name}`);
        }
      });
    }
  }

  function isJsonObject(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function assertFiniteJsonValue(value, path = "Configuration", ancestors = new Set()) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) throw new Error(`${path} must contain only finite JSON numbers`);
      return;
    }
    if (Array.isArray(value)) {
      if (ancestors.has(value)) throw new Error(`${path} must not contain cyclic values`);
      ancestors.add(value);
      value.forEach((item, index) => assertFiniteJsonValue(item, `${path}[${index}]`, ancestors));
      ancestors.delete(value);
      return;
    }
    if (isJsonObject(value)) {
      if (ancestors.has(value)) throw new Error(`${path} must not contain cyclic values`);
      ancestors.add(value);
      Object.entries(value).forEach(([name, item]) => {
        assertFiniteJsonValue(item, `${path}.${name}`, ancestors);
      });
      ancestors.delete(value);
      return;
    }
    throw new Error(`${path} must contain only JSON values`);
  }

  const STRUCTURED_ROOT_KEYS = new Set([
    "type", "title", "description", "properties", "required", "additionalProperties",
  ]);
  const STRUCTURED_SCALAR_KEYS = {
    boolean: new Set(["type", "title", "description", "default"]),
    integer: new Set([
      "type", "title", "description", "default",
      "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    ]),
    number: new Set([
      "type", "title", "description", "default",
      "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    ]),
    string: new Set(["type", "title", "description", "default", "enum"]),
  };
  const UNSAFE_STRUCTURED_PROPERTY_NAMES = new Set(["__proto__", "constructor", "prototype"]);

  function hasOnlySchemaKeys(schema, allowed) {
    return Object.keys(schema).every((name) => allowed.has(name));
  }

  function scalarDefaultSupported(value, type) {
    if (value === undefined) return true;
    if (type === "integer") return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value);
    if (type === "number") return typeof value === "number" && Number.isFinite(value);
    return typeof value === type;
  }

  function structuredPropertySchemaSupported(schema, required) {
    if (!isJsonObject(schema) || Array.isArray(schema.type) || typeof schema.type !== "string") return false;
    const type = schema.type;
    if (type === "object") {
      // Optional empty objects cannot be distinguished from an omitted value by
      // the structured editor, so an object field is structured only when it is
      // required and closed over explicitly declared properties.
      if (!required || !hasOnlySchemaKeys(schema, STRUCTURED_ROOT_KEYS)) return false;
      return structuredObjectSchemaSupported(schema);
    }
    const allowed = STRUCTURED_SCALAR_KEYS[type];
    if (!allowed || !hasOnlySchemaKeys(schema, allowed)) return false;
    if (Object.prototype.hasOwnProperty.call(schema, "default")
        && !scalarDefaultSupported(schema.default, type)) return false;
    if (type === "string") {
      if (schema.enum !== undefined) {
        if (!Array.isArray(schema.enum) || !schema.enum.length) return false;
        if (schema.enum.some((item) => typeof item !== "string" || item === "")) return false;
        if (new Set(schema.enum).size !== schema.enum.length) return false;
        if (schema.default !== undefined && !schema.enum.includes(schema.default)) return false;
      }
    }
    return true;
  }

  function structuredObjectSchemaSupported(schema) {
    if (!isJsonObject(schema) || schema.type !== "object" || !hasOnlySchemaKeys(schema, STRUCTURED_ROOT_KEYS)) {
      return false;
    }
    if (schema.additionalProperties !== false) return false;
    const properties = schema.properties === undefined ? {} : schema.properties;
    if (!isJsonObject(properties)) return false;
    const required = schema.required === undefined ? [] : schema.required;
    if (!Array.isArray(required)
        || required.some((name) => typeof name !== "string")
        || new Set(required).size !== required.length) return false;
    const propertyNames = Object.keys(properties);
    if (required.some((name) => !Object.prototype.hasOwnProperty.call(properties, name))) return false;
    if (propertyNames.some((name) => UNSAFE_STRUCTURED_PROPERTY_NAMES.has(name))) return false;
    const requiredNames = new Set(required);
    if (!propertyNames.every((name) => structuredPropertySchemaSupported(properties[name], requiredNames.has(name)))) {
      return false;
    }
    return true;
  }

  function structuredSchemaSupported(schema) {
    return structuredObjectSchemaSupported(schema);
  }

  function arrayValueText(value) {
    if (!Array.isArray(value)) return "";
    return value.map((item) => (typeof item === "object" ? JSON.stringify(item) : String(item ?? ""))).join("\n");
  }

  function parseArrayValue(text, spec = {}) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return [];
    const items = trimmed.includes("\n")
      ? trimmed.split("\n")
      : trimmed.split(",").map((item) => item.trim());
    const itemSpec = spec.items || {};
    const itemType = normalizedType(itemSpec);
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

  function parseObjectValue(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return {};
    return JSON.parse(trimmed);
  }

  function keyValueInput(spec = {}, value = "") {
    const type = normalizedType(spec);
    if (type === "boolean") {
      return `<select data-key-value-value data-value-type="boolean">
        <option value="true" ${value === true ? "selected" : ""}>true</option>
        <option value="false" ${value === false ? "selected" : ""}>false</option>
      </select>`;
    }
    const inputType = type === "number" || type === "integer" ? "number" : "text";
    const step = spec.multipleOf !== undefined
      ? spec.multipleOf
      : (type === "integer" ? "1" : (type === "number" ? "any" : ""));
    return `<input data-key-value-value data-value-type="${escapeHtml(type)}" type="${inputType}" ${step ? `step="${step}"` : ""} value="${escapeHtml(value ?? "")}" />`;
  }

  function keyValueRow(key, value, valueSpec = {}) {
    return `<div class="key-value-row">
      <input data-key-value-key type="text" aria-label="Parameter key" value="${escapeHtml(key)}" placeholder="Key" />
      ${keyValueInput(valueSpec, value)}
      <button data-remove-key-value type="button" aria-label="Remove parameter">Remove</button>
    </div>`;
  }

  function fieldHint(spec = {}, fallback = "") {
    return userFacingText(spec.description || fallback || "");
  }

  function isColorField(name, value, spec = {}) {
    const text = String(name || spec.title || "").toLowerCase();
    const candidate = String(value ?? spec.default ?? "").trim();
    return text.includes("color") || /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(candidate);
  }

  function colorEditor(fieldAttr, fieldName, current, metadata = "") {
    const safeValue = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(String(current || "").trim()) ? String(current).trim() : "#2563eb";
    return `
      <div class="color-input-row">
        <input
          ${fieldAttr}="${escapeHtml(fieldName)}"
          ${metadata}
          type="text"
          value="${escapeHtml(current ?? "")}"
          oninput="if(this.nextElementSibling){const v=this.value.trim();if(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)){this.nextElementSibling.value=v;}}"
        />
        <input
          type="color"
          value="${escapeHtml(safeValue)}"
          oninput="if(this.previousElementSibling){this.previousElementSibling.value=this.value;}"
        />
      </div>
    `;
  }

  function renderSchemaField(name, spec = {}, value, required = false, aliasState = schemaPresentationAliasState(value, spec)) {
    // `values` is the authoritative configuration being edited. Creation paths
    // supply defaults explicitly (for example with schemaDefaults(schema)); an
    // absent property in an existing configuration must stay absent instead of
    // acquiring JSON Schema's annotation-only `default` value as a side effect.
    const resolvedValue = value;
    if (spec?.const !== undefined || spec?.oneOf || spec?.anyOf || spec?.allOf) {
      const fixed = spec.const !== undefined;
      const sourceValue = fixed ? spec.const : resolvedValue;
      const jsonValue = sourceValue === undefined
        ? ""
        : JSON.stringify(schemaPresentationValue(sourceValue, aliasState), null, 2);
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(userFacingText(spec.title || humanizeName(name), "Field"))}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="json" data-schema-required="${required ? "1" : "0"}" spellcheck="false" ${fixed ? "readonly" : ""}>${escapeHtml(jsonValue)}</textarea>
          ${spec.description ? `<small class="field-hint">${escapeHtml(userFacingText(spec.description))}</small>` : ""}
        </label>
      `;
    }
    const type = normalizedType(spec);
    const label = userFacingText(spec.title || humanizeName(name), "Field");
    const hint = fieldHint(spec, type === "array" ? "One item per line." : "");
    if (type === "boolean") {
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <select data-schema-field="${escapeHtml(name)}" data-schema-type="boolean" data-schema-required="${required ? "1" : "0"}">
            ${!required || resolvedValue === undefined ? `<option value="" ${resolvedValue === undefined ? "selected" : ""}>${required ? "Select…" : "Not set"}</option>` : ""}
            <option value="true" ${resolvedValue === true ? "selected" : ""}>true</option>
            <option value="false" ${resolvedValue === false ? "selected" : ""}>false</option>
          </select>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (spec.enum?.length) {
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <select data-schema-field="${escapeHtml(name)}" data-schema-type="enum" data-schema-required="${required ? "1" : "0"}">
            ${!required || resolvedValue === undefined ? `<option value="" ${resolvedValue === undefined ? "selected" : ""}>${required ? "Select…" : "Not set"}</option>` : ""}
            ${spec.enum.map((item) => `<option value="${escapeHtml(item)}" ${String(item) === String(resolvedValue ?? "") ? "selected" : ""}>${escapeHtml(userFacingText(item, "Choice"))}</option>`).join("")}
          </select>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "array") {
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="array" data-schema-required="${required ? "1" : "0"}" spellcheck="false" placeholder="One item per line">${escapeHtml(arrayValueText(schemaPresentationValue(resolvedValue, aliasState)))}</textarea>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "object") {
      const properties = spec.properties || {};
      const valueSpec = spec.additionalProperties && typeof spec.additionalProperties === "object"
        ? spec.additionalProperties
        : {};
      if (!Object.keys(properties).length && spec.additionalProperties !== false) {
        const entries = resolvedValue && typeof resolvedValue === "object" && !Array.isArray(resolvedValue)
          ? Object.entries(resolvedValue)
          : [];
        return `
          <div class="structured-field structured-field-wide">
            <span>${escapeHtml(label)}${required ? " *" : ""}</span>
            <div class="key-value-editor" data-schema-field="${escapeHtml(name)}" data-schema-type="key-value" data-schema-required="${required ? "1" : "0"}" data-value-schema="${escapeHtml(JSON.stringify(valueSpec))}">
              <div data-key-value-rows>${entries.map(([key, child]) => keyValueRow(key, child, valueSpec)).join("")}</div>
              <button data-add-key-value type="button">Add mapping</button>
            </div>
            ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
          </div>
        `;
      }
      return `
        <div class="structured-field structured-field-wide structured-object" data-schema-field="${escapeHtml(name)}" data-schema-type="nested-object" data-schema-required="${required ? "1" : "0"}">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <div class="structured-fields-grid">
            ${Object.entries(properties).map(([childName, childSpec]) => renderSchemaField(
              childName,
              childSpec,
              resolvedValue?.[childName],
              new Set(spec.required || []).has(childName),
              aliasState,
            )).join("")}
          </div>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </div>
      `;
    }
    const inputType = type === "integer" || type === "number" ? "number" : "text";
    const step = type === "integer" ? "1" : (type === "number" ? "any" : "");
    const editor = type === "string" && isColorField(name, resolvedValue, spec)
      ? colorEditor(
        "data-schema-field",
        name,
        resolvedValue,
        `data-schema-type="string" data-schema-required="${required ? "1" : "0"}" data-schema-original-present="${resolvedValue === undefined ? "0" : "1"}"`,
      )
      : `
        <input
          data-schema-field="${escapeHtml(name)}"
          data-schema-type="${escapeHtml(type)}"
          data-schema-required="${required ? "1" : "0"}"
          data-schema-original-present="${resolvedValue === undefined ? "0" : "1"}"
          type="${inputType}"
          value="${escapeHtml(resolvedValue ?? "")}"
          ${spec.minimum !== undefined ? `min="${escapeHtml(spec.minimum)}"` : ""}
          ${spec.maximum !== undefined ? `max="${escapeHtml(spec.maximum)}"` : ""}
          ${spec.minLength !== undefined ? `minlength="${escapeHtml(spec.minLength)}"` : ""}
          ${spec.maxLength !== undefined ? `maxlength="${escapeHtml(spec.maxLength)}"` : ""}
          ${spec.pattern !== undefined ? `pattern="${escapeHtml(spec.pattern)}"` : ""}
          ${step ? `step="${step}"` : ""}
        />
      `;
    return `
      <label class="structured-field">
        <span>${escapeHtml(label)}${required ? " *" : ""}</span>
        ${editor}
        ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
      </label>
    `;
  }

  function renderRawSchemaFields(container, values = {}) {
    if (!isJsonObject(values)) throw new Error("Configuration must be a JSON object");
    assertFiniteJsonValue(values);
    const aliasState = schemaPresentationAliasState(values);
    schemaPresentationAliases.set(container, aliasState);
    const jsonValue = JSON.stringify(schemaPresentationValue(values, aliasState), null, 2);
    container.innerHTML = `
      <label class="structured-field structured-field-wide">
        <span>Configuration JSON object *</span>
        <textarea data-config-json-editor spellcheck="false" aria-label="Configuration JSON object">${escapeHtml(jsonValue)}</textarea>
        <small class="field-hint">This schema requires the full Draft 2020-12 contract. Enter one JSON object; Engine performs the authoritative schema validation.</small>
      </label>
    `;
    container.onclick = null;
  }

  function renderSchemaFields(container, schema = {}, values = {}) {
    if (!structuredSchemaSupported(schema)) {
      renderRawSchemaFields(container, values);
      return;
    }
    const properties = schema.properties || {};
    const required = new Set(schema.required || []);
    const entries = Object.entries(properties);
    const aliasState = schemaPresentationAliasState(values, schema);
    schemaPresentationAliases.set(container, aliasState);
    container.innerHTML = entries.length
      ? `<div class="structured-fields-grid">${entries.map(([name, spec]) => renderSchemaField(
        name, spec, values?.[name], required.has(name), aliasState,
      )).join("")}</div>`
      : '<div class="muted">No config fields</div>';
    container.onclick = (event) => {
      const remove = event.target.closest("[data-remove-key-value]");
      if (remove) {
        remove.closest(".key-value-row")?.remove();
        return;
      }
      const add = event.target.closest("[data-add-key-value]");
      if (!add) return;
      const editor = add.closest(".key-value-editor");
      let valueSpec = {};
      try {
        valueSpec = JSON.parse(editor?.dataset.valueSchema || "{}");
      } catch {}
      editor?.querySelector("[data-key-value-rows]")?.insertAdjacentHTML("beforeend", keyValueRow("", "", valueSpec));
      editor?.querySelector(".key-value-row:last-child [data-key-value-key]")?.focus();
    };
  }

  function parseKeyValueScalar(input) {
    const type = input.dataset.valueType || "string";
    if (type === "boolean") return input.value === "true";
    if (type === "integer") return input.value === "" ? undefined : Number.parseInt(input.value, 10);
    if (type === "number") return input.value === "" ? undefined : Number(input.value);
    return input.value;
  }

  function readSchemaFields(container, schema = {}, inheritedAliasState = null) {
    const aliasState = inheritedAliasState || schemaPresentationAliases.get(container) || schemaPresentationAliasState();
    const rawEditor = container.querySelector("[data-config-json-editor]");
    if (rawEditor) {
      let value;
      try {
        value = JSON.parse(rawEditor.value);
      } catch (error) {
        throw new Error(`Configuration must be valid JSON: ${error?.message || "parse failed"}`);
      }
      if (!isJsonObject(value)) throw new Error("Configuration must be a JSON object");
      assertFiniteJsonValue(value);
      return schemaPresentationValue(value, aliasState, true);
    }
    if (!structuredSchemaSupported(schema)) {
      throw new Error("Configuration requires the full JSON object editor");
    }
    const result = {};
    const properties = schema.properties || {};
    Object.entries(properties).forEach(([name, spec]) => {
      const input = container.querySelector(`[data-schema-field="${CSS.escape(name)}"]`);
      if (!input) return;
      const type = input.dataset.schemaType || normalizedType(spec);
      const required = input.dataset.schemaRequired === "1";
      let value;
      if (type === "boolean") value = input.value === "" ? undefined : input.value === "true";
      else if (type === "integer") value = input.value === "" ? undefined : Number(input.value);
      else if (type === "number") value = input.value === "" ? undefined : Number(input.value);
      else if (type === "array") value = !required && input.value.trim() === ""
        ? undefined
        : schemaPresentationValue(parseArrayValue(input.value, spec), aliasState, true);
      else if (type === "object") value = parseObjectValue(input.value);
      else if (type === "nested-object") value = readSchemaFields(input, spec, aliasState);
      else if (type === "json") {
        value = spec.const !== undefined
          ? JSON.parse(JSON.stringify(spec.const))
          : (input.value.trim() === "" ? undefined : schemaPresentationValue(JSON.parse(input.value), aliasState, true));
      }
      else if (type === "key-value") {
        value = {};
        input.querySelectorAll(".key-value-row").forEach((row) => {
          const key = row.querySelector("[data-key-value-key]")?.value?.trim() || "";
          const valueInput = row.querySelector("[data-key-value-value]");
          if (!key || !valueInput) return;
          if (Object.prototype.hasOwnProperty.call(value, key)) throw new Error(`Duplicate parameter key: ${key}`);
          const childValue = parseKeyValueScalar(valueInput);
          if (childValue !== undefined) value[key] = childValue;
        });
      }
      else value = input.value;
      if (!required && type === "nested-object" && !Object.keys(value || {}).length) return;
      if (!required && type === "key-value" && !Object.keys(value || {}).length) return;
      if (value === undefined) return;
      if (!required && (type === "string" || type === "enum") && value === ""
          && input.dataset.schemaOriginalPresent !== "1") return;
      validateSchemaValue(value, spec, spec.title || humanizeName(name));
      result[name] = value;
    });
    validateSchemaValue(result, schema, schema.title || "Configuration");
    return result;
  }

  let searchableComboboxSequence = 0;

  function normalizeSearchText(value) {
    return String(value || "")
      .normalize("NFKD")
      .toLocaleLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function fuzzyOptionScore(query, option = {}) {
    const needle = normalizeSearchText(query);
    if (!needle) return 1;
    const value = normalizeSearchText(option.value);
    const label = normalizeSearchText(option.label);
    const path = normalizeSearchText(option.path);
    const meta = normalizeSearchText(option.meta);
    const haystack = [label, value, path, meta].filter(Boolean).join(" ");
    if (!haystack) return -1;
    if (value === needle || label === needle) return 2000;
    if (value.startsWith(needle) || label.startsWith(needle)) return 1600 - needle.length;
    const directIndex = haystack.indexOf(needle);
    if (directIndex >= 0) return 1200 - directIndex;
    let cursor = 0;
    let gap = 0;
    let contiguous = 0;
    let previous = -2;
    for (const character of needle.replaceAll(" ", "")) {
      const index = haystack.indexOf(character, cursor);
      if (index < 0) return -1;
      gap += index - cursor;
      if (index === previous + 1) contiguous += 1;
      previous = index;
      cursor = index + 1;
    }
    return 700 + contiguous * 4 - gap;
  }

  function normalizeComboboxOptions(options = []) {
    const seen = new Set();
    return options.flatMap((option) => {
      const value = String(option?.value || "").trim();
      if (!value || seen.has(value)) return [];
      seen.add(value);
      const rawPath = String(option?.path || value).trim();
      const segments = rawPath.split(".").map((segment) => segment.trim()).filter(Boolean);
      return [{
        ...option,
        value,
        label: userFacingText(option?.label || value, "Choice"),
        path: segments.join("."),
        segments: segments.length ? segments : [value],
        meta: String(option?.meta || ""),
        disabled: Boolean(option?.disabled),
      }];
    });
  }

  function buildComboboxTree(options) {
    const root = { name: "All Data", path: "", parent: null, children: new Map(), option: null, optionCount: 0 };
    const nodesByPath = new Map([["", root]]);
    options.forEach((option) => {
      let node = root;
      let path = "";
      option.segments.forEach((segment) => {
        path = path ? `${path}.${segment}` : segment;
        if (!node.children.has(segment)) {
          const child = { name: segment, path, parent: node, children: new Map(), option: null, optionCount: 0 };
          node.children.set(segment, child);
          nodesByPath.set(path, child);
        }
        node = node.children.get(segment);
      });
      node.option = option;
    });
    const countOptions = (node) => {
      node.optionCount = (node.option ? 1 : 0)
        + [...node.children.values()].reduce((total, child) => total + countOptions(child), 0);
      return node.optionCount;
    };
    countOptions(root);
    return { root, nodesByPath };
  }

  function comboboxShell(inputHtml, menuId, emptyText) {
    return `
      <div class="search-tree-combobox" data-search-tree-combobox>
        ${inputHtml}
        <button class="search-tree-toggle" data-combobox-toggle type="button" aria-label="Open hierarchical choices" tabindex="-1">▾</button>
        <div class="search-tree-menu" data-combobox-menu id="${escapeHtml(menuId)}" role="tree" hidden>
          <div class="search-tree-toolbar">
            <button class="search-tree-back" data-combobox-back type="button" aria-label="Back one level">← Back</button>
            <div class="search-tree-breadcrumb" data-combobox-breadcrumb></div>
            <span data-combobox-count></span>
            <button class="search-tree-close" data-combobox-close type="button" aria-label="Close choices">×</button>
          </div>
          <div class="search-tree-results" data-combobox-results data-empty-text="${escapeHtml(emptyText)}"></div>
        </div>
      </div>
    `;
  }

  function initializeSearchTreeCombobox(host, rawOptions, config = {}) {
    const input = host.querySelector("input");
    const toggle = host.querySelector("[data-combobox-toggle]");
    const menu = host.querySelector("[data-combobox-menu]");
    const back = host.querySelector("[data-combobox-back]");
    const closeButton = host.querySelector("[data-combobox-close]");
    const breadcrumb = host.querySelector("[data-combobox-breadcrumb]");
    const count = host.querySelector("[data-combobox-count]");
    const results = host.querySelector("[data-combobox-results]");
    const options = normalizeComboboxOptions(rawOptions);
    const optionByValue = new Map(options.map((option) => [option.value, option]));
    const { root, nodesByPath } = buildComboboxTree(options);
    let currentNode = root;
    let query = "";
    let activeIndex = -1;
    let selectedValue = String(config.selectedValue || "");
    let suppressFocusOpen = false;
    let controller = null;

    const selectedOption = () => optionByValue.get(selectedValue) || null;
    const selectedText = () => {
      const option = selectedOption();
      return option ? userFacingText(config.displayValue?.(option) || option.label || option.value, option.label || "Choice") : "";
    };
    const isOpen = () => !menu.hidden;

    const focusInputWithoutOpening = () => {
      suppressFocusOpen = true;
      try {
        input.focus({ preventScroll: true });
      } catch {
        input.focus();
      } finally {
        // HTMLElement.focus() dispatches focus synchronously.  Clear the guard
        // immediately so the user's next click can open the menu normally.
        suppressFocusOpen = false;
      }
    };

    const closeOpenPeers = () => {
      document.querySelectorAll("[data-search-tree-combobox]").forEach((peer) => {
        if (peer === host) return;
        peer.__searchTreeComboboxController?.dismiss?.();
      });
    };

    const positionMenu = () => {
      if (!isOpen()) return;
      menu.classList.remove("open-up", "align-right");
      menu.style.removeProperty("--search-tree-space");

      const inputRect = input.getBoundingClientRect();
      const viewportHeight = window.visualViewport?.height || window.innerHeight;
      const viewportWidth = window.visualViewport?.width || window.innerWidth;
      const spaceBelow = Math.max(0, viewportHeight - inputRect.bottom - 8);
      const spaceAbove = Math.max(0, inputRect.top - 8);
      const desiredHeight = Math.min(440, menu.scrollHeight);
      const openUp = spaceBelow < desiredHeight && spaceAbove > spaceBelow;
      const availableSpace = Math.max(96, Math.floor(openUp ? spaceAbove : spaceBelow));

      menu.classList.toggle("open-up", openUp);
      menu.style.setProperty("--search-tree-space", `${availableSpace}px`);
      const menuRect = menu.getBoundingClientRect();
      menu.classList.toggle("align-right", menuRect.right > viewportWidth - 8);
    };

    const choiceHtml = (option, { search = false, segment = "" } = {}) => {
      const meta = userFacingText(option.meta || "");
      const title = userFacingText(search ? option.label : (segment || option.label), "Choice");
      const detail = userFacingText(
        option.detail || (search ? option.value : (option.value === title ? meta : option.value)),
      );
      return `<button type="button" class="search-tree-choice" data-combobox-choice="${escapeHtml(option.value)}" role="treeitem" ${option.disabled ? "disabled" : ""}>
        <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></span>
        ${meta ? `<em>${escapeHtml(meta)}</em>` : ""}
      </button>`;
    };

    const updateActive = (nextIndex) => {
      const choices = [...results.querySelectorAll("[data-combobox-choice]:not(:disabled), [data-combobox-branch]")];
      if (!choices.length) {
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
        return;
      }
      activeIndex = Math.max(0, Math.min(nextIndex, choices.length - 1));
      choices.forEach((choice, index) => {
        choice.classList.toggle("active", index === activeIndex);
        if (!choice.id) choice.id = `${menu.id}-choice-${index}`;
      });
      input.setAttribute("aria-activedescendant", choices[activeIndex].id);
      choices[activeIndex].scrollIntoView?.({ block: "nearest" });
    };

    const renderBreadcrumb = () => {
      const nodes = [];
      for (let node = currentNode; node; node = node.parent) nodes.unshift(node);
      breadcrumb.innerHTML = nodes.map((node, index) => (
        `<button type="button" data-combobox-path="${escapeHtml(node.path)}" ${index === nodes.length - 1 ? "aria-current=\"page\"" : ""}>${escapeHtml(node === root ? (config.rootLabel || "All") : userFacingText(node.name, "Group"))}</button>`
      )).join('<span aria-hidden="true">›</span>');
      back.textContent = "← Back";
      back.disabled = currentNode === root;
    };

    const render = () => {
      activeIndex = -1;
      const trimmedQuery = query.trim();
      if (trimmedQuery) {
        const matches = options
          .map((option) => ({ option, score: fuzzyOptionScore(trimmedQuery, option) }))
          .filter((entry) => entry.score >= 0)
          .sort((left, right) => right.score - left.score || left.option.value.localeCompare(right.option.value));
        const shown = matches.slice(0, 100);
        count.textContent = `${matches.length} match${matches.length === 1 ? "" : "es"}${matches.length > shown.length ? ` · first ${shown.length}` : ""}`;
        breadcrumb.innerHTML = `<span>Search all ${options.length} ${escapeHtml(config.collectionLabel || "choices")}</span>`;
        back.textContent = "← Clear";
        back.disabled = false;
        results.innerHTML = shown.length
          ? shown.map(({ option }) => choiceHtml(option, { search: true })).join("")
          : `<div class="search-tree-empty">No compatible choice matches “${escapeHtml(trimmedQuery)}”.</div>`;
        positionMenu();
        return;
      }
      renderBreadcrumb();
      count.textContent = `${currentNode.optionCount} ${escapeHtml(config.collectionLabel || "choices")}`;
      const rows = [];
      if (currentNode !== root && currentNode.option) {
        rows.push(`<div class="search-tree-current-choice"><span>Select this ${escapeHtml(config.choiceLabel || "choice")}</span>${choiceHtml(currentNode.option, { search: true })}</div>`);
      }
      [...currentNode.children.values()]
        .sort((left, right) => left.name.localeCompare(right.name))
        .forEach((node) => {
          const option = node.option;
          rows.push(`<div class="search-tree-row">
            ${node.children.size
              ? `<button type="button" class="search-tree-branch-main" data-combobox-branch="${escapeHtml(node.path)}" role="treeitem" aria-label="Open ${escapeHtml(userFacingText(node.name, "group"))}"><span><strong>${escapeHtml(userFacingText(node.name, "Group"))}</strong><small>${escapeHtml(userFacingText(node.path, "Group"))}</small></span><em><span>${node.optionCount}</span>›</em></button>`
              : choiceHtml(option, { segment: node.name })}
          </div>`);
        });
      results.innerHTML = rows.length
        ? rows.join("")
        : `<div class="search-tree-empty">${escapeHtml(results.dataset.emptyText || "No compatible choices are available.")}</div>`;
      positionMenu();
    };

    const open = ({ browse = false } = {}) => {
      if (isOpen()) return;
      closeOpenPeers();
      if (browse && (input.value === selectedText() || input.value === selectedValue)) query = "";
      else query = input.value;
      if (browse) currentNode = root;
      menu.hidden = false;
      input.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-expanded", "true");
      render();
      positionMenu();
    };

    const close = ({ restore = false } = {}) => {
      menu.hidden = true;
      input.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
      if (restore) input.value = selectedText();
    };

    const commit = (option) => {
      if (!option || option.disabled) return;
      selectedValue = option.value;
      input.value = userFacingText(config.displayValue?.(option) || option.label || option.value, option.label || "Choice");
      query = "";
      config.onSelect?.(option);
      close();
      focusInputWithoutOpening();
    };

    results.addEventListener("click", (event) => {
      const choice = event.target.closest("[data-combobox-choice]");
      if (choice) {
        commit(optionByValue.get(choice.dataset.comboboxChoice));
        return;
      }
      const branch = event.target.closest("[data-combobox-branch]");
      if (!branch) return;
      currentNode = nodesByPath.get(branch.dataset.comboboxBranch) || root;
      query = "";
      render();
      input.focus();
    });
    breadcrumb.addEventListener("click", (event) => {
      const path = event.target.closest("[data-combobox-path]")?.dataset.comboboxPath;
      if (path === undefined) return;
      currentNode = nodesByPath.get(path) || root;
      query = "";
      render();
      input.focus();
    });
    back.addEventListener("click", () => {
      if (query) {
        query = "";
        input.value = "";
        currentNode = root;
      } else {
        currentNode = currentNode.parent || root;
      }
      query = "";
      render();
      input.focus();
    });
    closeButton.addEventListener("click", () => {
      close({ restore: Boolean(config.restoreOnClose) });
      focusInputWithoutOpening();
    });
    toggle.addEventListener("click", () => {
      if (isOpen()) {
        close({ restore: Boolean(config.restoreOnClose) });
        focusInputWithoutOpening();
        return;
      }
      open({ browse: true });
      focusInputWithoutOpening();
    });
    input.addEventListener("focus", () => {
      if (suppressFocusOpen) return;
      if (!isOpen()) open({ browse: true });
    });
    input.addEventListener("click", () => {
      // Escape and the explicit close control intentionally leave focus on the
      // input.  A later click does not emit another focus event, so it needs an
      // explicit reopen path.
      if (!isOpen()) open({ browse: true });
    });
    input.addEventListener("input", () => {
      query = input.value;
      currentNode = root;
      config.onQuery?.(input.value, optionByValue.get(input.value) || null);
      if (!isOpen()) open();
      else render();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close({ restore: Boolean(config.restoreOnClose) });
        return;
      }
      if (event.key === "ArrowLeft" && !query && currentNode !== root) {
        event.preventDefault();
        currentNode = currentNode.parent || root;
        render();
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!isOpen()) open({ browse: true });
        updateActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
        return;
      }
      if (event.key !== "Enter" || !isOpen()) return;
      const choices = [...results.querySelectorAll("[data-combobox-choice]:not(:disabled), [data-combobox-branch]")];
      const active = activeIndex >= 0 ? choices[activeIndex] : null;
      if (active) {
        event.preventDefault();
        active.click();
        return;
      }
      const exact = options.find((option) => !option.disabled && (
        option.value === input.value || option.label === input.value
      ));
      if (exact) {
        event.preventDefault();
        commit(exact);
      }
    });
    input.value = selectedText() || input.value;
    input.dataset.optionCount = String(options.length);
    controller = {
      dismiss() {
        close({ restore: Boolean(config.restoreOnClose) });
      },
      setSelectedValue(value) {
        selectedValue = String(value || "");
        input.value = selectedText();
        if (isOpen()) render();
      },
      options,
    };
    host.__searchTreeComboboxController = controller;
    return controller;
  }

  function enhanceSearchableSelect(select, config = {}) {
    if (!select || select.__searchTreeCombobox) return select?.__searchTreeCombobox || null;
    const menuId = `search-tree-menu-${++searchableComboboxSequence}`;
    const inputId = `${menuId}-input`;
    const options = [...select.options]
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        label: option.textContent || option.value,
        path: option.dataset.comboboxPath || option.value,
        meta: option.dataset.comboboxMeta || "",
        detail: option.dataset.comboboxDetail || "",
        disabled: option.disabled,
      }));
    const host = document.createElement("div");
    host.innerHTML = comboboxShell(
      `<input id="${inputId}" type="text" role="combobox" aria-label="${escapeHtml(config.ariaLabel || config.placeholder || "Search or browse")}" aria-autocomplete="list" aria-haspopup="tree" aria-controls="${menuId}" aria-expanded="false" autocomplete="off" spellcheck="false" placeholder="${escapeHtml(config.placeholder || "Search or browse")}" />`,
      menuId,
      config.emptyText || "No choices are available.",
    );
    const combobox = host.firstElementChild;
    select.classList.add("search-tree-native-select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.insertAdjacentElement("afterend", combobox);
    const controller = initializeSearchTreeCombobox(combobox, options, {
      selectedValue: select.value,
      displayValue: (option) => option.label,
      rootLabel: config.rootLabel || "All",
      collectionLabel: config.collectionLabel || "choices",
      choiceLabel: config.choiceLabel || "choice",
      restoreOnClose: true,
      onSelect(option) {
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      },
    });
    select.addEventListener("change", () => controller.setSelectedValue(select.value));
    select.__searchTreeCombobox = controller;
    return controller;
  }

  function renderParamField(definition = {}, value, options = []) {
    const name = definition.name;
    const type = definition.type || "string";
    const label = userFacingText(definition.label || humanizeName(name), "Parameter");
    const hint = userFacingText(definition.description || "");
    const resolvedValue = value ?? definition.default ?? "";
    if (type === "boolean") {
      return `
        <label class="structured-field structured-field-checkbox">
          <span>${escapeHtml(label)}</span>
          <input data-param-field="${escapeHtml(name)}" data-param-type="boolean" type="checkbox" ${resolvedValue ? "checked" : ""} />
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "dataKey") {
      const menuId = `datakey-menu-${++searchableComboboxSequence}`;
      const inputId = `${menuId}-input`;
      return `
        <div class="structured-field">
          <label for="${inputId}">${escapeHtml(label)}</label>
          <div data-datakey-combobox="${escapeHtml(name)}">
            ${comboboxShell(
              `<input id="${inputId}" data-param-field="${escapeHtml(name)}" data-param-type="dataKey" type="text" role="combobox" aria-autocomplete="list" aria-haspopup="tree" aria-controls="${menuId}" aria-expanded="false" value="${escapeHtml(resolvedValue || "")}" placeholder="Search or browse compatible DataKeys" autocomplete="off" spellcheck="false" />`,
              menuId,
              "No compatible DataKeys were declared by this Result.",
            )}
          </div>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </div>
      `;
    }
    if (definition.options?.length) {
      const normalizedOptions = definition.options.map((option) => (
        option && typeof option === "object" && !Array.isArray(option)
          ? { value: String(option.value ?? ""), label: userFacingText(option.label ?? option.value, "Choice") }
          : { value: String(option), label: userFacingText(option, "Choice") }
      ));
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}</span>
          <select data-param-field="${escapeHtml(name)}" data-param-type="select">
            ${normalizedOptions.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === String(resolvedValue) ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}
          </select>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    const inputType = type === "integer" || type === "number" ? "number" : "text";
    const step = type === "integer" ? "1" : (type === "number" ? "any" : "");
    const editor = type === "string" && isColorField(name, resolvedValue, definition)
      ? colorEditor("data-param-field", name, resolvedValue)
      : `
        <input
          data-param-field="${escapeHtml(name)}"
          data-param-type="${escapeHtml(type)}"
          type="${inputType}"
          value="${escapeHtml(resolvedValue ?? "")}"
          ${definition.min !== undefined ? `min="${escapeHtml(definition.min)}"` : ""}
          ${definition.max !== undefined ? `max="${escapeHtml(definition.max)}"` : ""}
          ${step ? `step="${step}"` : ""}
        />
      `;
    return `
      <label class="structured-field">
        <span>${escapeHtml(label)}</span>
        ${editor}
        ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
      </label>
    `;
  }

  function renderParamFields(container, definitions = [], values = {}, optionMap = {}, behavior = {}) {
    container.innerHTML = definitions.length
      ? `<div class="structured-fields-grid">${definitions.map((definition) => renderParamField(definition, values?.[definition.name], optionMap?.[definition.name] || [])).join("")}</div>`
      : '<div class="muted">No fields</div>';
    container.querySelectorAll("[data-datakey-combobox]").forEach((wrapper) => {
      const name = wrapper.dataset.datakeyCombobox;
      const host = wrapper.querySelector("[data-search-tree-combobox]");
      const field = host.querySelector('[data-param-type="dataKey"]');
      const options = (optionMap?.[name] || []).map((option) => ({
        ...option,
        path: option.value,
        meta: option.dataType || schemaTypeLabel(option.schema),
      }));
      if (behavior.autoSelectSingle !== false && !field.value && options.length === 1) {
        field.value = options[0].value;
        field.dataset.selectedValue = options[0].value;
      }
      initializeSearchTreeCombobox(host, options, {
        selectedValue: field.value,
        displayValue: (option) => option.value,
        rootLabel: "All Data",
        collectionLabel: "compatible DataKeys",
        choiceLabel: "DataKey",
        restoreOnClose: true,
        onQuery(_text, exact) {
          field.dataset.selectedValue = exact?.value || "";
        },
        onSelect(option) {
          field.dataset.selectedValue = option.value;
          field.dispatchEvent(new Event("change", { bubbles: true }));
        },
      });
    });
  }

  function readParamFields(container, definitions = []) {
    const result = {};
    definitions.forEach((definition) => {
      const input = container.querySelector(`[data-param-field="${CSS.escape(definition.name)}"]`);
      if (!input) return;
      const type = input.dataset.paramType || definition.type || "string";
      let value;
      if (type === "boolean") value = !!input.checked;
      else if (type === "integer") value = input.value === "" ? undefined : Number.parseInt(input.value, 10);
      else if (type === "number") value = input.value === "" ? undefined : Number(input.value);
      else value = input.value;
      if (value === undefined) return;
      if (type === "string" && value === "") return;
      if (type === "dataKey" && value === "") return;
      result[definition.name] = value;
    });
    return result;
  }

  function renderPortFields(container, ports = {}, values = {}, defaults = {}, behavior = {}) {
    const entries = Object.entries(ports || {});
    container.innerHTML = entries.length
      ? `<div class="structured-fields-grid">${entries.map(([name, spec]) => {
        const value = values?.[name] ?? defaults?.[name] ?? "";
        const compactHint = `${schemaTypeLabel(spec?.schema)} · ${spec?.required === false ? "Optional" : "Required"}`;
        const hint = behavior.compactHints ? compactHint : compactHint;
        return `
          <label class="structured-field">
            <span>${escapeHtml(name)}</span>
            <input data-port-field="${escapeHtml(name)}" data-default-value="${escapeHtml(defaults?.[name] ?? "")}" type="text" value="${escapeHtml(value)}" />
            <small class="field-hint">${escapeHtml(hint)}</small>
          </label>
        `;
      }).join("")}</div>`
      : '<div class="muted">No ports</div>';
  }

  function readPortFields(container, ports = {}) {
    const result = {};
    Object.keys(ports || {}).forEach((name) => {
      const input = container.querySelector(`[data-port-field="${CSS.escape(name)}"]`);
      if (!input) return;
      const value = String(input.value || "").trim();
      if (value) result[name] = value;
    });
    return result;
  }

  function syncPortDefaults(container, defaults = {}) {
    Object.entries(defaults || {}).forEach(([name, next]) => {
      const input = container.querySelector(`[data-port-field="${CSS.escape(name)}"]`);
      if (!input) return;
      const previous = input.dataset.defaultValue || "";
      if (!input.value || input.value === previous) input.value = next;
      input.dataset.defaultValue = next;
    });
  }

  window.TradeModuleForms = {
    escapeHtml,
    humanizeName,
    opaqueMachineIdentityKind,
    userFacingText,
    resourceDisplayName,
    presentationJson,
    normalizedType,
    schemaTypeLabel,
    schemaDefaults,
    structuredSchemaSupported,
    validateSchemaValue,
    renderSchemaFields,
    renderParamFields,
    enhanceSearchableSelect,
    fuzzyOptionScore,
    readSchemaFields,
    readParamFields,
    renderPortFields,
    readPortFields,
    syncPortDefaults,
  };
}());
