#!/usr/bin/env python3
"""Generic UI contracts for passive application protocol metadata."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def section(start: str, end: str) -> str:
    return APP_SOURCE.split(start, 1)[1].split(end, 1)[0]


class ProtocolMetadataUiTests(unittest.TestCase):
    def test_optional_protocol_id_is_omitted_only_for_an_empty_value(self):
        helper = section(
            "function optionalProtocolId(value) {",
            "function activePipelineUiDocument()",
        )
        self.assertIn('value === "" || typeof value === "undefined"', helper)
        self.assertIn("{ protocolId: value }", helper)
        self.assertNotIn("trim", helper)

    def test_module_upload_imports_and_submits_the_optional_id(self):
        self.assertIn('id="moduleUploadProtocolId"', HTML_SOURCE)
        populate = section(
            "function setModuleUploadDefinition(definition = {}) {",
            "function openModuleUploadDialog()",
        )
        submit = section(
            '$("moduleUploadForm")?.addEventListener("submit",',
            '$("createPipelineName")?.addEventListener',
        )
        self.assertIn('$("moduleUploadProtocolId").value = definition.protocolId || "";', populate)
        self.assertIn('...optionalProtocolId($("moduleUploadProtocolId").value)', submit)

    def test_pipeline_create_load_and_version_save_preserve_the_optional_id(self):
        for field_id in ("createPipelineProtocolId", "pipelineProtocolId"):
            self.assertIn(f'id="{field_id}"', HTML_SOURCE)
        create = section(
            '$("createPipelineForm")?.addEventListener("submit",',
            '$("loadPipelineBtn").addEventListener',
        )
        clone = section("function clonePipelineDraft(definition = {}) {", "function pipelinePinnedInstanceIds")
        load = section("function loadPipelineFormFromDefinition(options = {}) {", "function draftInstances()")
        payload = section("function buildPipelinePayload() {", "async function saveCurrentPipelineVersion")
        field_state = section("function syncPipelineDraftFieldState() {", "function syncGlobalNavActionState()")
        self.assertIn('...optionalProtocolId($("createPipelineProtocolId").value)', create)
        self.assertIn('protocolId: definition.protocolId || ""', clone)
        self.assertIn('protocolId: sourceDefinition.protocolId || ""', load)
        self.assertIn('pipelineField("ProtocolId").value = meta.protocolId || ""', load)
        self.assertIn('["Id", "Name", "ProtocolId", "AlphaGraph"]', field_state)
        self.assertEqual(payload.count("optionalProtocolId"), 1)
        self.assertIn('...optionalProtocolId(pipelineField("ProtocolId").value)', payload)
        self.assertIn('instances: { ...(state.pipelineDraft?.instances || {}) }', payload)

    def test_graph_resource_editors_keep_protocol_metadata_at_the_draft_root(self):
        for resource, end in (
            ("Analysis", "function renderBlueprintMeta"),
            ("Environment", "async function loadSummary"),
        ):
            body = section(
                f"function render{resource}Details() {{",
                end,
            )
            self.assertIn('key: "protocolId"', body)
            self.assertIn("draft.protocolId = values.protocolId", body)
            self.assertEqual(body.count("...optionalProtocolId(draft.protocolId)"), 2)
            self.assertIn("instances: draft.instances", body)
            self.assertIn("graph: draft.graph", body)

    def test_agent_draft_sync_accepts_optional_protocol_metadata(self):
        for name, identity in (
            ("Pipeline", "pipelineId"),
            ("Environment", "environmentId"),
            ("Analysis", "analysisId"),
        ):
            body = section(
                f"function active{name}UiDocument() {{",
                "function active" + ({
                    "Pipeline": "Environment",
                    "Environment": "Analysis",
                    "Analysis": "Backtest",
                }[name]) + "UiDocument()",
            )
            self.assertIn('"protocolId"', body)
            self.assertIn("...optionalProtocolId(", body)
            self.assertIn(identity, body)

    def test_protocol_metadata_does_not_enter_backtest_runtime_composition(self):
        backtest = section(
            "function buildBacktestCompositionRequest() {",
            "function renderBacktestCompositionStatus()",
        )
        self.assertNotIn("protocolId", backtest)


if __name__ == "__main__":
    unittest.main()
