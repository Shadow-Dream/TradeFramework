#!/usr/bin/env python3
"""Browser-independent regression tests for Module config-schema editing."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_FORMS = ROOT / "web" / "module_forms.js"
MODULE_GRAPH = ROOT / "web" / "module_graph_litegraph.js"
MODULE_FORMS_SOURCE = MODULE_FORMS.read_text(encoding="utf-8")
STYLES = ROOT / "web" / "styles.css"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the web form regression test")
class ModuleFormsSchemaFallbackTest(unittest.TestCase):
    def test_visible_schema_json_and_enum_hide_identities_but_round_trip_exact_values(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
global.window = {};
vm.runInThisContext(fs.readFileSync('web/module_forms.js', 'utf8'));
const forms = window.TradeModuleForms;
const ulid = '01M0Z2D7FZACAARVSZPZH34P63';
const exact = {
  backtestId: `bt_${ulid}`,
  nested: { contentDigest: `sha256:${'a'.repeat(64)}`, target: `viz_${ulid}` },
};
const container = {
  innerHTML: '',
  onclick: null,
  querySelector() { return this.editor || null; },
};
forms.renderSchemaFields(container, { type: 'object' }, exact);
assert.equal(container.innerHTML.includes(`bt_${ulid}`), false);
assert.equal(container.innerHTML.includes('a'.repeat(64)), false);
const encoded = container.innerHTML.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/)[1];
const decoded = encoded
  .replaceAll('&quot;', '"')
  .replaceAll('&lt;', '<')
  .replaceAll('&gt;', '>')
  .replaceAll('&amp;', '&');
container.editor = { value: decoded };
assert.equal(JSON.stringify(forms.readSchemaFields(container, { type: 'object' })), JSON.stringify(exact));

const enumContainer = { innerHTML: '', onclick: null, querySelectorAll() { return []; } };
forms.renderSchemaFields(enumContainer, {
  type: 'object', additionalProperties: false,
  properties: { target: { type: 'string', enum: [`viz_${ulid}`] } },
}, { target: `viz_${ulid}` });
assert.equal(enumContainer.innerHTML.includes(`>${`viz_${ulid}`}<`), false);
assert.equal(enumContainer.innerHTML.includes(`value="viz_${ulid}"`), true);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_internal_select_values_can_have_separate_user_facing_labels(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {}, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/module_forms.js', 'utf8'), context);
const container = {
  innerHTML: '',
  querySelectorAll() { return []; },
};
context.window.TradeModuleForms.renderParamFields(container, [{
  name: 'target', type: 'visualizerRef',
  options: [
    { value: '', label: 'Select…' },
    { value: 'ohlc.candles.m9v0z7q2', label: 'Candles' },
  ],
}], { target: 'ohlc.candles.m9v0z7q2' });
process.stdout.write(container.innerHTML);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('value="ohlc.candles.m9v0z7q2"', completed.stdout)
        self.assertIn(">Candles</option>", completed.stdout)
        self.assertNotIn(">ohlc.candles.m9v0z7q2</option>", completed.stdout)
        self.assertIn("comboboxDetail", MODULE_FORMS_SOURCE)

    def test_datakey_picker_is_one_hierarchical_search_combobox(self):
        self.assertIn('role="combobox"', MODULE_FORMS_SOURCE)
        self.assertIn('aria-haspopup="tree"', MODULE_FORMS_SOURCE)
        self.assertIn("fuzzyOptionScore", MODULE_FORMS_SOURCE)
        self.assertIn("data-combobox-branch", MODULE_FORMS_SOURCE)
        self.assertIn('classList.toggle("open-up", openUp)', MODULE_FORMS_SOURCE)
        self.assertIn("getBoundingClientRect", MODULE_FORMS_SOURCE)
        self.assertIn('class="search-tree-back"', MODULE_FORMS_SOURCE)
        self.assertIn('class="search-tree-close"', MODULE_FORMS_SOURCE)
        self.assertNotIn('class="search-tree-branch"', MODULE_FORMS_SOURCE)
        self.assertNotIn('host.addEventListener("focusout"', MODULE_FORMS_SOURCE)
        self.assertIn("options.length === 1", MODULE_FORMS_SOURCE)
        self.assertIn("displayValue: (option) => option.value", MODULE_FORMS_SOURCE)
        self.assertIn("No compatible DataKeys were declared by this Result.", MODULE_FORMS_SOURCE)
        self.assertNotIn('<details class="datakey-picker">', MODULE_FORMS_SOURCE)
        self.assertNotIn("Browse declared DataKeys", MODULE_FORMS_SOURCE)

    def test_search_combobox_real_browser_focus_click_and_scroll_lifecycle(self):
        chrome = next((
            path for name in (
                "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
            )
            if (path := shutil.which(name))
        ), None)
        if not chrome:
            self.skipTest("Chrome or Chromium is required for the combobox browser regression test")

        scroll_options = "".join(
            f'<option value="group.item{index}" data-combobox-path="group.item{index}">Item {index}</option>'
            for index in range(60)
        )
        document = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <link rel="stylesheet" href="{STYLES.as_uri()}" />
  </head>
  <body>
    <select id="first">
      <option value=""></option>
      <option value="series.line" data-combobox-path="series.line">Line</option>
      <option value="series.scatter" data-combobox-path="series.scatter">Scatter</option>
    </select>
    <select id="second">
      <option value=""></option>
      <option value="beta">Beta</option>
    </select>
    <select id="scroll"><option value=""></option>{scroll_options}</select>
    <button id="disabledButton" disabled>Unavailable</button>
    <pre id="result"></pre>
    <script src="{MODULE_FORMS.as_uri()}"></script>
    <script>
      const forms = window.TradeModuleForms;
      for (const id of ["first", "second", "scroll"]) {{
        forms.enhanceSearchableSelect(document.getElementById(id), {{ restoreOnClose: true }});
      }}

      const firstSelect = document.getElementById("first");
      const firstHost = firstSelect.nextElementSibling;
      const firstInput = firstHost.querySelector("input");
      const firstMenu = firstHost.querySelector("[data-combobox-menu]");
      const firstClose = firstHost.querySelector("[data-combobox-close]");
      const firstBack = firstHost.querySelector("[data-combobox-back]");
      const firstResults = firstHost.querySelector("[data-combobox-results]");

      firstInput.focus();
      const initiallyOpen = !firstMenu.hidden;
      firstClose.focus();
      firstClose.click();
      const closeStaysClosed = firstMenu.hidden && document.activeElement === firstInput;
      firstInput.click();
      const focusedInputClickReopens = !firstMenu.hidden;

      let branch = firstResults.querySelector('[data-combobox-branch="series"]');
      branch.focus();
      branch.click();
      const branchOpened = !firstBack.disabled && firstResults.querySelector('[data-combobox-choice="series.line"]');
      firstBack.focus();
      firstBack.click();
      const backReturnedToRoot = firstBack.disabled && firstResults.querySelector('[data-combobox-branch="series"]');

      branch = firstResults.querySelector('[data-combobox-branch="series"]');
      branch.focus();
      branch.click();
      const lineChoice = firstResults.querySelector('[data-combobox-choice="series.line"]');
      lineChoice.focus();
      lineChoice.click();
      const commitStaysClosed = firstMenu.hidden
        && firstSelect.value === "series.line"
        && document.activeElement === firstInput;
      firstInput.click();
      const clickReopensAfterCommit = !firstMenu.hidden;
      firstInput.dispatchEvent(new KeyboardEvent("keydown", {{ key: "Escape", bubbles: true }}));
      const escapeClosed = firstMenu.hidden;
      firstInput.click();
      const clickReopensAfterEscape = !firstMenu.hidden;

      const secondHost = document.getElementById("second").nextElementSibling;
      const secondInput = secondHost.querySelector("input");
      const secondMenu = secondHost.querySelector("[data-combobox-menu]");
      secondInput.focus();
      const openingPeerClosesFirst = firstMenu.hidden && !secondMenu.hidden;

      const scrollHost = document.getElementById("scroll").nextElementSibling;
      const scrollInput = scrollHost.querySelector("input");
      const scrollMenu = scrollHost.querySelector("[data-combobox-menu]");
      const scrollResults = scrollHost.querySelector("[data-combobox-results]");
      scrollInput.focus();
      const scrollBranch = scrollResults.querySelector('[data-combobox-branch="group"]');
      scrollBranch.focus();
      scrollBranch.click();
      scrollResults.scrollTop = 160;
      const listScrollsInsideOverlay = scrollResults.scrollHeight > scrollResults.clientHeight
        && scrollResults.scrollTop > 0
        && getComputedStyle(scrollResults).overflowY === "auto";
      const overlayIsRaised = getComputedStyle(scrollHost).zIndex === "121"
        && getComputedStyle(scrollMenu).pointerEvents === "auto";

      const disabledStyle = getComputedStyle(document.getElementById("disabledButton"));
      const disabledIsVisuallyExplicit = disabledStyle.cursor === "not-allowed"
        && Number(disabledStyle.opacity) < 1;

      document.getElementById("result").textContent = JSON.stringify({{
        initiallyOpen,
        closeStaysClosed,
        focusedInputClickReopens,
        branchOpened: Boolean(branchOpened),
        backReturnedToRoot: Boolean(backReturnedToRoot),
        commitStaysClosed,
        clickReopensAfterCommit,
        escapeClosed,
        clickReopensAfterEscape,
        openingPeerClosesFirst,
        listScrollsInsideOverlay,
        overlayIsRaised,
        disabledIsVisuallyExplicit,
      }});
    </script>
  </body>
</html>"""
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            page = root_path / "combobox.html"
            page.write_text(document, encoding="utf-8")
            completed = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--window-size=800,420",
                    f"--user-data-dir={root_path / 'profile'}",
                    "--dump-dom",
                    page.as_uri(),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        match = re.search(r'<pre id="result">(.*?)</pre>', completed.stdout, re.DOTALL)
        self.assertIsNotNone(match, msg=completed.stdout)
        result = json.loads(html.unescape(match.group(1)))
        self.assertTrue(all(result.values()), msg=result)

    def test_analysis_input_source_labels_round_trip_to_canonical_source(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

global.window = {};
const filename = process.argv[1];
const original = fs.readFileSync(filename, "utf8");
const instrumented = original.replace(
  "window.ModuleGraphLiteGraph = { mount };",
  "window.ModuleGraphLiteGraph = { mount, inputSourceModel, graphInputBoundary, inferredModuleDefinition };",
);
assert.notEqual(instrumented, original);
vm.runInThisContext(instrumented, { filename });
const helpers = window.ModuleGraphLiteGraph;
const model = helpers.inputSourceModel(
  { currentPipeline: "Current completed Pipeline" },
  "Current Sample + prior Pipeline",
);
assert.deepEqual(model.namedSources, ["currentPipeline"]);
assert.deepEqual(model.sources, ["", "currentPipeline"]);
assert.deepEqual(
  model.widgetValues,
  ["Current Sample + prior Pipeline", "Current completed Pipeline"],
);
assert.equal(model.sourceByLabel["Current Sample + prior Pipeline"], "");
assert.equal(model.sourceByLabel["Current completed Pipeline"], "currentPipeline");
assert.deepEqual(
  helpers.graphInputBoundary("market.cycle", "wire.cycle", "", true),
  { dataKey: "market.cycle", wire: "wire.cycle" },
);
assert.deepEqual(
  helpers.graphInputBoundary(
    "market.cycle",
    "wire.cycle",
    model.sourceByLabel["Current completed Pipeline"],
    true,
  ),
  { dataKey: "market.cycle", wire: "wire.cycle", source: "currentPipeline" },
);

const unresolved = helpers.inferredModuleDefinition({
  kind: "Signal",
  moduleId: "renamed-module",
  version: "old-revision",
  inputs: { source: "wire.source" },
  outputs: { result: "wire.result" },
});
assert.equal(unresolved.unresolved, true);
assert.deepEqual(Object.keys(unresolved.ports.inputs), ["source"]);
assert.deepEqual(Object.keys(unresolved.ports.outputs), ["result"]);

const duplicateLabels = helpers.inputSourceModel(
  { first: "Same", second: "Same" },
  "Same",
);
assert.equal(new Set(duplicateLabels.widgetValues).size, 3);
assert.deepEqual(
  duplicateLabels.widgetValues.map((label) => duplicateLabels.sourceByLabel[label]),
  ["", "first", "second"],
);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(MODULE_GRAPH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_unsupported_schema_uses_one_raw_json_object_editor(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

global.window = {};
global.CSS = { escape: (value) => String(value) };
const filename = process.argv[1];
vm.runInThisContext(fs.readFileSync(filename, "utf8"), { filename });
const forms = window.TradeModuleForms;
assert.ok(forms.fuzzyOptionScore("pae", {
  value: "portfolio.account.equity",
  label: "portfolio.account.equity",
  path: "portfolio.account.equity",
}) >= 0);
assert.equal(forms.fuzzyOptionScore("zzz", {
  value: "portfolio.account.equity",
  label: "portfolio.account.equity",
  path: "portfolio.account.equity",
}), -1);

const simple = {
  type: "object",
  properties: {
    enabled: { type: "boolean", default: true },
    threshold: { type: "number", minimum: 0 },
    mode: { type: "string", enum: ["fast", "safe"] },
    nested: {
      type: "object",
      properties: { count: { type: "integer", minimum: 0 } },
      required: ["count"],
      additionalProperties: false,
    },
  },
  required: ["nested"],
  additionalProperties: false,
};
assert.equal(forms.structuredSchemaSupported(simple), true);

const optionalStringDefault = {
  type: "object",
  properties: {
    instrumentId: { type: "string", default: "SPX_CFD" },
    evaluationStart: { type: "string" },
  },
  additionalProperties: false,
};
assert.equal(forms.structuredSchemaSupported(optionalStringDefault), true);
const optionalDefaultRendered = { innerHTML: "", onclick: null };
forms.renderSchemaFields(optionalDefaultRendered, optionalStringDefault, { instrumentId: "" });
assert.doesNotMatch(optionalDefaultRendered.innerHTML, /data-config-json-editor/);
assert.match(optionalDefaultRendered.innerHTML, /data-schema-field="instrumentId"/);
assert.match(optionalDefaultRendered.innerHTML, /data-schema-field="evaluationStart"/);
assert.match(optionalDefaultRendered.innerHTML, /data-schema-original-present="1"/);
const explicitBlankInput = {
  value: "",
  dataset: {
    schemaType: "string",
    schemaRequired: "0",
    schemaOriginalPresent: "1",
  },
};
assert.deepEqual(forms.readSchemaFields({
  querySelector(selector) {
    if (selector === "[data-config-json-editor]") return null;
    if (selector === '[data-schema-field="instrumentId"]') return explicitBlankInput;
    return null;
  },
}, optionalStringDefault), { instrumentId: "" });
explicitBlankInput.dataset.schemaOriginalPresent = "0";
assert.deepEqual(forms.readSchemaFields({
  querySelector(selector) {
    if (selector === "[data-config-json-editor]") return null;
    if (selector === '[data-schema-field="instrumentId"]') return explicitBlankInput;
    return null;
  },
}, optionalStringDefault), {});

const nestedNot = {
  type: "object",
  properties: {
    nested: {
      type: "object",
      properties: { mode: { type: "string", not: { const: "blocked" } } },
      required: ["mode"],
      additionalProperties: false,
    },
  },
  required: ["nested"],
  additionalProperties: false,
};
assert.equal(forms.structuredSchemaSupported(nestedNot), false);

const nestedDependentRequired = {
  type: "object",
  properties: {
    nested: {
      type: "object",
      properties: {
        left: { type: "number" },
        right: { type: "number" },
      },
      dependentRequired: { left: ["right"] },
      additionalProperties: false,
    },
  },
  required: ["nested"],
  additionalProperties: false,
};
assert.equal(forms.structuredSchemaSupported(nestedDependentRequired), false);

for (const schema of [
  { ...simple, if: { properties: { enabled: { const: true } } }, then: { required: ["threshold"] } },
  { ...simple, dependentRequired: { enabled: ["threshold"] } },
  { ...simple, propertyNames: { pattern: "^[a-z]+$" } },
  { ...simple, patternProperties: { "^x-": { type: "string" } } },
  { ...simple, unevaluatedProperties: false },
  { ...simple, $defs: { value: { type: "number" } } },
  { ...simple, allOf: [{ type: "object" }] },
  { ...simple, anyOf: [{ type: "object" }] },
  { ...simple, oneOf: [{ type: "object" }] },
  {
    type: "object",
    properties: { values: { type: "array", contains: { const: 1 } } },
    additionalProperties: false,
  },
  {
    type: "object",
    properties: { value: { $ref: "#/$defs/value" } },
    additionalProperties: false,
  },
]) {
  assert.equal(forms.structuredSchemaSupported(schema), false);
}

const structured = { innerHTML: "", onclick: null };
forms.renderSchemaFields(structured, simple, { enabled: true, nested: { count: 1 } });
assert.doesNotMatch(structured.innerHTML, /data-config-json-editor/);
assert.match(structured.innerHTML, /Not set/);

const rendered = { innerHTML: "", onclick: () => {} };
forms.renderSchemaFields(rendered, nestedNot, { nested: { mode: "blocked" } });
assert.match(rendered.innerHTML, /data-config-json-editor/);
assert.match(rendered.innerHTML, /Configuration JSON object/);
assert.match(rendered.innerHTML, /Engine performs the authoritative schema validation/);
assert.equal(rendered.onclick, null);

function rawContainer(value) {
  return {
    querySelector(selector) {
      assert.equal(selector, "[data-config-json-editor]");
      return { value };
    },
  };
}

// "blocked" violates the unsupported `not` constraint. The raw editor must
// preserve it and let the Engine's Draft 2020-12 validator return the semantic
// error instead of applying an incomplete browser-side validator.
assert.deepEqual(
  forms.readSchemaFields(rawContainer('{"nested":{"mode":"blocked"}}'), nestedNot),
  { nested: { mode: "blocked" } },
);
assert.throws(
  () => forms.readSchemaFields(rawContainer('[1,2]'), nestedNot),
  /must be a JSON object/,
);
assert.throws(
  () => forms.readSchemaFields(rawContainer('{"value":1e400}'), nestedNot),
  /finite JSON numbers/,
);
assert.throws(
  () => forms.readSchemaFields(rawContainer('{broken'), nestedNot),
  /must be valid JSON/,
);
assert.throws(
  () => forms.readSchemaFields({ querySelector: () => null }, nestedNot),
  /requires the full JSON object editor/,
);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(MODULE_FORMS)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
