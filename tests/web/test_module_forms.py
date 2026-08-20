#!/usr/bin/env python3
"""Browser-independent regression tests for Module config-schema editing."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_FORMS = ROOT / "web" / "module_forms.js"
MODULE_GRAPH = ROOT / "web" / "module_graph_litegraph.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the web form regression test")
class ModuleFormsSchemaFallbackTest(unittest.TestCase):
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
  "window.ModuleGraphLiteGraph = { mount, inputSourceModel, graphInputBoundary };",
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
