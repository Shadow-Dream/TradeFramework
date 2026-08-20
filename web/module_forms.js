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
      } else if (!required) {
        // A blank optional text input currently means "not set", so it cannot
        // also preserve the distinct valid value "".
        return false;
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
    return spec.description || fallback || "";
  }

  function isColorField(name, value, spec = {}) {
    const text = String(name || spec.title || "").toLowerCase();
    const candidate = String(value ?? spec.default ?? "").trim();
    return text.includes("color") || /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(candidate);
  }

  function colorEditor(fieldAttr, fieldName, current) {
    const safeValue = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(String(current || "").trim()) ? String(current).trim() : "#2563eb";
    return `
      <div class="color-input-row">
        <input
          ${fieldAttr}="${escapeHtml(fieldName)}"
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

  function renderSchemaField(name, spec = {}, value, required = false) {
    // `values` is the authoritative configuration being edited. Creation paths
    // supply defaults explicitly (for example with schemaDefaults(schema)); an
    // absent property in an existing configuration must stay absent instead of
    // acquiring JSON Schema's annotation-only `default` value as a side effect.
    const resolvedValue = value;
    if (spec?.const !== undefined || spec?.oneOf || spec?.anyOf || spec?.allOf) {
      const fixed = spec.const !== undefined;
      const jsonValue = resolvedValue === undefined ? "" : JSON.stringify(resolvedValue, null, 2);
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(spec.title || humanizeName(name))}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="json" data-schema-required="${required ? "1" : "0"}" spellcheck="false" ${fixed ? "readonly" : ""}>${escapeHtml(fixed ? JSON.stringify(spec.const) : jsonValue)}</textarea>
          ${spec.description ? `<small class="field-hint">${escapeHtml(spec.description)}</small>` : ""}
        </label>
      `;
    }
    const type = normalizedType(spec);
    const label = spec.title || humanizeName(name);
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
            ${spec.enum.map((item) => `<option value="${escapeHtml(item)}" ${String(item) === String(resolvedValue ?? "") ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}
          </select>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "array") {
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="array" data-schema-required="${required ? "1" : "0"}" spellcheck="false" placeholder="One item per line">${escapeHtml(arrayValueText(resolvedValue))}</textarea>
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
            )).join("")}
          </div>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </div>
      `;
    }
    const inputType = type === "integer" || type === "number" ? "number" : "text";
    const step = type === "integer" ? "1" : (type === "number" ? "any" : "");
    const editor = type === "string" && isColorField(name, resolvedValue, spec)
      ? colorEditor("data-schema-field", name, resolvedValue)
      : `
        <input
          data-schema-field="${escapeHtml(name)}"
          data-schema-type="${escapeHtml(type)}"
          data-schema-required="${required ? "1" : "0"}"
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
    const jsonValue = JSON.stringify(values, null, 2);
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
    container.innerHTML = entries.length
      ? `<div class="structured-fields-grid">${entries.map(([name, spec]) => renderSchemaField(
        name, spec, values?.[name], required.has(name),
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

  function readSchemaFields(container, schema = {}) {
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
      return value;
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
      else if (type === "array") value = !required && input.value.trim() === "" ? undefined : parseArrayValue(input.value, spec);
      else if (type === "object") value = parseObjectValue(input.value);
      else if (type === "nested-object") value = readSchemaFields(input, spec);
      else if (type === "json") value = input.value.trim() === "" ? undefined : JSON.parse(input.value);
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
      if (!required && (type === "string" || type === "enum") && value === "") return;
      validateSchemaValue(value, spec, spec.title || humanizeName(name));
      result[name] = value;
    });
    validateSchemaValue(result, schema, schema.title || "Configuration");
    return result;
  }

  function renderParamField(definition = {}, value, options = []) {
    const name = definition.name;
    const type = definition.type || "string";
    const label = definition.label || humanizeName(name);
    const hint = definition.description || "";
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
      const tree = {};
      options.forEach((option) => {
        let level = tree;
        String(option.value || "").split(".").forEach((segment, index, parts) => {
          level[segment] ||= { children: {}, option: null };
          if (index === parts.length - 1) level[segment].option = option;
          level = level[segment].children;
        });
      });
      const renderNodes = (nodes) => `<ul class="datakey-menu-level">${Object.entries(nodes).map(([segment, node]) => {
        const children = Object.keys(node.children).length ? renderNodes(node.children) : "";
        const choice = node.option
          ? `<button type="button" class="datakey-choice" data-datakey-choice="${escapeHtml(node.option.value)}" data-datakey-label="${escapeHtml(node.option.label || node.option.value)}"><span>${escapeHtml(segment)}</span><small>${escapeHtml(node.option.dataType || schemaTypeLabel(node.option.schema))}</small></button>`
          : `<span class="datakey-branch-label"><span>${escapeHtml(segment)}</span><small>object</small></span>`;
        return `<li class="datakey-menu-node ${children ? "has-children" : ""}">${choice}${children}</li>`;
      }).join("")}</ul>`;
      const selected = options.find((option) => String(option.value) === String(resolvedValue));
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}</span>
          <input data-param-field="${escapeHtml(name)}" data-param-type="dataKey" type="text" value="${escapeHtml(resolvedValue || "")}" placeholder="DataKey path" autocomplete="off" spellcheck="false" />
          <details class="datakey-picker">
            <summary data-datakey-summary>${escapeHtml(selected?.label || resolvedValue || "Browse declared DataKeys")}</summary>
            <div class="datakey-menu">${renderNodes(tree)}</div>
          </details>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (definition.options?.length) {
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}</span>
          <select data-param-field="${escapeHtml(name)}" data-param-type="select">
            ${definition.options.map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(resolvedValue) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
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

  function renderParamFields(container, definitions = [], values = {}, optionMap = {}) {
    container.innerHTML = definitions.length
      ? `<div class="structured-fields-grid">${definitions.map((definition) => renderParamField(definition, values?.[definition.name], optionMap?.[definition.name] || [])).join("")}</div>`
      : '<div class="muted">No fields</div>';
    container.querySelectorAll(".datakey-picker").forEach((picker) => {
      const field = picker.parentElement.querySelector('[data-param-type="dataKey"]');
      const summary = picker.querySelector("[data-datakey-summary]");
      field?.addEventListener("input", () => {
        summary.textContent = field.value.trim() || "Browse declared DataKeys";
      });
      picker.querySelectorAll("[data-datakey-choice]").forEach((button) => {
        button.addEventListener("click", () => {
          field.value = button.dataset.datakeyChoice || "";
          summary.textContent = button.dataset.datakeyLabel || field.value;
          picker.removeAttribute("open");
          field.dispatchEvent(new Event("change", { bubbles: true }));
        });
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

  function renderPortFields(container, ports = {}, values = {}, defaults = {}) {
    const entries = Object.entries(ports || {});
    container.innerHTML = entries.length
      ? `<div class="structured-fields-grid">${entries.map(([name, spec]) => {
        const value = values?.[name] ?? defaults?.[name] ?? "";
        const hint = [JSON.stringify(spec?.schema || {}), spec?.required === false ? "optional" : "required"].join(" / ");
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
    normalizedType,
    schemaTypeLabel,
    schemaDefaults,
    structuredSchemaSupported,
    validateSchemaValue,
    renderSchemaFields,
    renderParamFields,
    readSchemaFields,
    readParamFields,
    renderPortFields,
    readPortFields,
    syncPortDefaults,
  };
}());
