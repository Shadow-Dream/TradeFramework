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

  function schemaDefaults(schema = {}) {
    const properties = schema.properties || {};
    return Object.fromEntries(Object.entries(properties)
      .filter(([, spec]) => Object.prototype.hasOwnProperty.call(spec || {}, "default"))
      .map(([name, spec]) => [name, spec.default]));
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

  function renderSchemaField(name, spec = {}, value) {
    const type = normalizedType(spec);
    const label = spec.title || humanizeName(name);
    const required = Array.isArray(spec.required) ? spec.required.includes(name) : false;
    const hint = fieldHint(spec, type === "array" ? "One item per line." : "");
    if (type === "boolean") {
      return `
        <label class="structured-field structured-field-checkbox">
          <span>${escapeHtml(label)}</span>
          <input data-schema-field="${escapeHtml(name)}" data-schema-type="boolean" type="checkbox" ${value ? "checked" : ""} />
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (spec.enum?.length) {
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <select data-schema-field="${escapeHtml(name)}" data-schema-type="enum">
            ${spec.enum.map((item) => `<option value="${escapeHtml(item)}" ${String(item) === String(value ?? "") ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}
          </select>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "array") {
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="array" spellcheck="false" placeholder="One item per line">${escapeHtml(arrayValueText(value))}</textarea>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    if (type === "object") {
      return `
        <label class="structured-field structured-field-wide">
          <span>${escapeHtml(label)}${required ? " *" : ""}</span>
          <textarea data-schema-field="${escapeHtml(name)}" data-schema-type="object" spellcheck="false" placeholder='{"key":"value"}'>${escapeHtml(value && typeof value === "object" ? JSON.stringify(value, null, 2) : "")}</textarea>
          ${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}
        </label>
      `;
    }
    const inputType = type === "integer" || type === "number" ? "number" : "text";
    const step = type === "integer" ? "1" : (type === "number" ? "any" : "");
    const editor = type === "string" && isColorField(name, value, spec)
      ? colorEditor("data-schema-field", name, value)
      : `
        <input
          data-schema-field="${escapeHtml(name)}"
          data-schema-type="${escapeHtml(type)}"
          type="${inputType}"
          value="${escapeHtml(value ?? "")}"
          ${spec.minimum !== undefined ? `min="${escapeHtml(spec.minimum)}"` : ""}
          ${spec.maximum !== undefined ? `max="${escapeHtml(spec.maximum)}"` : ""}
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

  function renderSchemaFields(container, schema = {}, values = {}) {
    const properties = schema.properties || {};
    const entries = Object.entries(properties);
    container.innerHTML = entries.length
      ? `<div class="structured-fields-grid">${entries.map(([name, spec]) => renderSchemaField(name, spec, values?.[name])).join("")}</div>`
      : '<div class="muted">No config fields</div>';
  }

  function readSchemaFields(container, schema = {}) {
    const result = {};
    const properties = schema.properties || {};
    Object.entries(properties).forEach(([name, spec]) => {
      const input = container.querySelector(`[data-schema-field="${CSS.escape(name)}"]`);
      if (!input) return;
      const type = input.dataset.schemaType || normalizedType(spec);
      let value;
      if (type === "boolean") value = !!input.checked;
      else if (type === "integer") value = input.value === "" ? undefined : Number.parseInt(input.value, 10);
      else if (type === "number") value = input.value === "" ? undefined : Number(input.value);
      else if (type === "array") value = parseArrayValue(input.value, spec);
      else if (type === "object") value = parseObjectValue(input.value);
      else value = input.value;
      if (value === undefined) return;
      if (type === "string" && value === "") return;
      result[name] = value;
    });
    return result;
  }

  function renderParamField(definition = {}, value, options = []) {
    const name = definition.name;
    const type = definition.type || "string";
    const label = definition.label || humanizeName(name);
    const hint = definition.description || "";
    const resolvedValue = value ?? definition.default ?? (type === "dataKey" ? options[0]?.value ?? "" : "");
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
      return `
        <label class="structured-field">
          <span>${escapeHtml(label)}</span>
          <select data-param-field="${escapeHtml(name)}" data-param-type="dataKey">
            <option value=""></option>
            ${options.map((option) => `<option value="${escapeHtml(option.value)}" ${String(option.value) === String(resolvedValue) ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}
          </select>
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
        const hint = [spec?.type || "any", spec?.required === false ? "optional" : "required"].join(" / ");
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
    schemaDefaults,
    renderSchemaFields,
    renderParamFields,
    readSchemaFields,
    readParamFields,
    renderPortFields,
    readPortFields,
    syncPortDefaults,
  };
}());
