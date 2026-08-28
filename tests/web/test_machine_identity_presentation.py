#!/usr/bin/env python3
"""Regression coverage for machine-identity presentation boundaries."""

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
GRAPH = ROOT / "web" / "module_graph_litegraph.js"
BROWSER_SOURCE = ROOT / "web_src" / "trade_resource_browser.jsx"
BROWSER_BUNDLE = ROOT / "web" / "vendor" / "trade-resource-browser.js"


class MachineIdentitySourceContractTests(unittest.TestCase):
    def test_graph_visible_surfaces_do_not_interpolate_module_or_instance_ids(self):
        source = GRAPH.read_text(encoding="utf-8")
        for forbidden in (
            "<small>${escapeHtml(module.moduleId)}</small>",
            "`${item.definition.moduleId} · ${item.id}`",
            "`${item.id}.${port} is required.`",
            "escapeHtml(authorityValidation.message)",
            "statusElement.textContent = message ||",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("function graphFacingText(value, fallback = \"\")", source)
        self.assertIn("modulePresentationMeta(item.definition)", source)

    def test_resource_browser_has_no_item_id_fallback_or_truncated_identity_suffix(self):
        source = BROWSER_SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "item.label || item.itemId",
            "String(item.itemId).slice(-10)",
            '["ID", record.itemId]',
            "JSON.stringify(port?.schema || {})",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("resourceDisplayName(item, repository)", source)
        self.assertIn("name = `${base} · ${ordinal}`", source)
        self.assertIn("resourceBrowserError(error, catalog, repository)", source)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ModuleGraphPresentationHelperTests(unittest.TestCase):
    def test_graph_helpers_redact_machine_identities_without_changing_internal_keys(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
global.window = { TradeModuleForms: { humanizeName(value) {
  return String(value || "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
} } };
const original = fs.readFileSync(process.argv[1], "utf8");
const instrumented = original.replace(
  "window.ModuleGraphLiteGraph = { mount };",
  "window.ModuleGraphLiteGraph = { mount, userFacingText, moduleDisplayName, modulePresentationMeta, inferredModuleDefinition };",
);
assert.notEqual(instrumented, original);
vm.runInThisContext(instrumented, { filename: process.argv[1] });
const p = window.ModuleGraphLiteGraph;
const ulid = "01M0Z2D7FZACAARVSZPZH34P63";
const digest = `sha256:${"a".repeat(64)}`;
const message = p.userFacingText(`Failed mod_${ulid} in module-${"b".repeat(32)} with ${digest}`);
assert.equal(message.includes(ulid), false);
assert.equal(message.includes("b".repeat(32)), false);
assert.equal(message.includes("a".repeat(64)), false);
for (const identity of [
  ulid,
  `folder_${ulid}`,
  `bars-daily-${"c".repeat(24)}`,
  "ohlc.candles.mt7e4ksv",
]) {
  assert.equal(p.userFacingText(identity).includes(identity), false, identity);
  assert.equal(p.userFacingText(`Failed ${identity} while loading`).includes(identity), false, identity);
}
assert.equal(p.moduleDisplayName({ kind: "Signal", moduleId: "private.signal", status: "archived" }), "Signal Module · Archived");
assert.equal(p.moduleDisplayName({ kind: "Signal", moduleId: "private.signal", name: "Friendly Signal", version: 3 }), "Friendly Signal");
assert.equal(p.moduleDisplayName({ kind: "Signal", moduleId: "private.signal", name: "private.signal", version: 3 }), "Signal Module · v3");
assert.equal(p.modulePresentationMeta({ kind: "Signal", moduleId: "private.signal", version: 3 }), "Signal Module · v3");
const unresolved = p.inferredModuleDefinition({ kind: "Signal", moduleId: `mod_${ulid}`, version: 7, inputs: {}, outputs: {} });
assert.equal(unresolved.name.includes(ulid), false);
assert.equal(unresolved.moduleId, `mod_${ulid}`);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(GRAPH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)


class ResourceBrowserPresentationHelperTests(unittest.TestCase):
    def test_built_browser_redacts_ids_objects_errors_and_uses_ordinals(self):
        chrome = next(
            (
                path
                for name in (
                    "google-chrome-stable",
                    "google-chrome",
                    "chromium",
                    "chromium-browser",
                )
                if (path := shutil.which(name))
            ),
            None,
        )
        if not chrome:
            self.skipTest("Chrome or Chromium is required")

        with tempfile.TemporaryDirectory() as temp_root:
            page = Path(temp_root) / "presentation.html"
            page.write_text(
                f"""<!doctype html>
<html><body><div id="browser"></div><pre id="result"></pre>
<script src="{BROWSER_BUNDLE.as_uri()}"></script>
<script>
const p = window.TradeResourceBrowserPresentation;
const ulid = "01M0Z2D7FZACAARVSZPZH34P63";
const digest = `sha256:${{"a".repeat(64)}}`;
const catalog = {{ items: [
  {{ itemId: `pipe_${{ulid}}`, pipelineId: "private-pipeline-key", resourceType: "Pipeline", label: "Momentum", status: "active", version: 1, folderPath: "/" }},
  {{ itemId: `pipe_01M0Z2D7FZACAARVSZPZH34P64`, pipelineId: "second-private-key", resourceType: "Pipeline", label: "Momentum", status: "active", version: 2, folderPath: "/" }},
], folders: [
  {{ folderId: `folder_${{ulid}}`, name: `folder_${{ulid}}`, path: `/folder_${{ulid}}` }},
] }};
const files = p.catalogFiles(catalog, "pipelines");
const projected = p.presentationValue({{ itemId: `pipe_${{ulid}}`, contentDigest: digest, nested: {{ instanceId: `inst_${{"b".repeat(32)}}`, title: `Uses mod_${{ulid}}` }}, protocolId: "basic" }});
const output = {{
  text: p.userFacingText(`Failed pipe_${{ulid}} with ${{digest}}`),
  unnamed: p.resourceDisplayName({{ itemId: `pipe_${{ulid}}`, label: `pipe_${{ulid}}`, resourceType: "Pipeline", status: "archived" }}, "pipelines"),
  names: files.filter((entry) => !entry.isDirectory).map((entry) => entry.name),
  folderNames: files.filter((entry) => entry.isDirectory).map((entry) => entry.name),
  internalIds: files.filter((entry) => !entry.isDirectory).map((entry) => entry.tradeItemId),
  projected,
  error: p.resourceBrowserError("Could not open private-pipeline-key", catalog, "pipelines"),
  fallbackCoverage: [
    ulid,
    `folder_${{ulid}}`,
    `bars-daily-${{"c".repeat(24)}}`,
    "ohlc.candles.mt7e4ksv",
  ].map((identity) => [p.opaqueMachineIdentityKind(identity), p.userFacingText(`Failed ${{identity}}`)]),
}};
window.TradeResourceBrowser.mount(document.getElementById("browser"), {{
  repository: "pipelines",
  catalog,
  readOnly: true,
  onMutation: async () => {{}},
  onOpen: async () => {{}},
  onResourceAction: async () => {{}},
}});
setTimeout(() => {{
  const browser = document.getElementById("browser");
  output.surface = [
    browser.innerText,
    ...[...browser.querySelectorAll("[title], [aria-label]")].flatMap((node) => [node.title, node.getAttribute("aria-label")]),
  ].filter(Boolean).join(" ");
  document.getElementById("result").textContent = JSON.stringify(output);
}}, 120);
</script></body></html>""",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--virtual-time-budget=1500",
                    f"--user-data-dir={Path(temp_root) / 'profile'}",
                    "--dump-dom",
                    page.as_uri(),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        match = re.search(r'<pre id="result">(.*?)</pre>', completed.stdout, re.DOTALL)
        self.assertIsNotNone(match, msg=completed.stdout)
        result = json.loads(html.unescape(match.group(1)))
        rendered = json.dumps(
            {key: value for key, value in result.items() if key != "internalIds"},
            sort_keys=True,
        )
        self.assertNotIn("01M0Z2D7FZACAARVSZPZH34P63", rendered)
        self.assertNotIn("a" * 64, rendered)
        self.assertNotIn("b" * 32, rendered)
        self.assertNotIn("private-pipeline-key", rendered)
        self.assertEqual(result["unnamed"], "Pipeline · Archived")
        self.assertEqual(result["names"], ["Momentum", "Momentum · 2"])
        self.assertEqual(result["folderNames"], ["Folder"])
        self.assertEqual(result["internalIds"][0], "pipe_01M0Z2D7FZACAARVSZPZH34P63")
        self.assertNotIn("itemId", result["projected"])
        self.assertNotIn("contentDigest", result["projected"])
        self.assertEqual(result["projected"]["protocolId"], "basic")


if __name__ == "__main__":
    unittest.main()
