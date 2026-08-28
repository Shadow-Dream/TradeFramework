import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pipeline_module_picker_smoke.js"


def run_node(source: str) -> str:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is required")
    return subprocess.check_output(
        [node, "-e", source],
        cwd=ROOT,
        text=True,
    )


class PipelineModulePickerSmokeContractTests(unittest.TestCase):
    def test_script_has_valid_javascript_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required")
        subprocess.run(
            [node, "--check", str(SCRIPT)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_contract_covers_picker_hierarchy_and_round_trip_layout(self):
        contract = json.loads(run_node(
            "const {CONTRACT}=require('./scripts/pipeline_module_picker_smoke.js');"
            "process.stdout.write(JSON.stringify(CONTRACT));"
        ))
        self.assertEqual(contract["targetStage"], "target")
        self.assertEqual(
            contract["pipelineStages"],
            ["universe", "signal", "target", "constraint"],
        )
        self.assertEqual(contract["singleStages"], ["universe", "target"])
        self.assertEqual(contract["containerStages"], ["signal", "constraint"])
        self.assertEqual(
            contract["stageCardModes"],
            {
                "container": "container",
                "singleModule": "single-module",
                "singleSlot": "single-slot",
            },
        )
        self.assertEqual(contract["menuVariant"], "pipeline-module")
        self.assertLessEqual(contract["maxMenuItemHeight"], 56)
        self.assertEqual(contract["currentIdentityFields"], ["kind", "moduleId"])
        self.assertEqual(contract["pipelineRoute"], "/pipeline/builder")
        self.assertEqual(contract["signalRoute"], "/signal-blueprint")
        self.assertEqual(
            contract["stageModuleTagSelector"],
            ".pipeline-stage-tag-list.loaded-tags "
            ".loaded-tag[data-configure-stage-module][data-instance]",
        )
        self.assertEqual(
            contract["singleModuleSelector"],
            ".pipeline-single-module-card[data-single-stage-module]",
        )
        self.assertEqual(
            contract["singleModuleConfigureSelector"],
            ".pipeline-single-module-configure"
            "[data-configure-stage-module][data-instance]",
        )
        self.assertEqual(
            contract["singleModuleMetadataSelector"],
            ".pipeline-single-module-identity[data-single-module-meta]",
        )
        self.assertEqual(contract["singleSlotSelector"], ".pipeline-single-stage-slot")
        self.assertEqual(contract["stageContainerSelector"], ".pipeline-stage-container")
        self.assertEqual(
            contract["stageTagListSelector"],
            ".pipeline-stage-tag-list.loaded-tags",
        )
        self.assertEqual(contract["stageCardWidth"], 210)
        self.assertEqual(contract["stageTagHeight"], 28)
        self.assertLessEqual(contract["stageTrailingGapMax"], 14)
        self.assertEqual(
            contract["stageKinds"],
            {
                "universe": "Universe",
                "signal": "Signal",
                "target": "Target",
                "constraint": "Constraint",
            },
        )
        self.assertIn("viewport", contract["geometrySelectors"])
        self.assertIn("board", contract["geometrySelectors"])
        self.assertIn("transform", contract["computedStyleProperties"])
        self.assertIn("justifyContent", contract["computedStyleProperties"])

    def test_snapshot_comparison_allows_subpixel_geometry_only(self):
        result = json.loads(run_node(
            "const {snapshotDifferences}=require('./scripts/pipeline_module_picker_smoke.js');"
            "const base={node:{rect:{x:10,width:210},style:{fontSize:'13px'}}};"
            "const close={node:{rect:{x:10.4,width:210.4},style:{fontSize:'13px'}}};"
            "const moved={node:{rect:{x:11,width:210},style:{fontSize:'13px'}}};"
            "const restyled={node:{rect:{x:10,width:210},style:{fontSize:'16px'}}};"
            "process.stdout.write(JSON.stringify({"
            "close:snapshotDifferences(base,close),"
            "moved:snapshotDifferences(base,moved),"
            "restyled:snapshotDifferences(base,restyled)}));"
        ))
        self.assertEqual(result["close"], [])
        self.assertTrue(result["moved"])
        self.assertTrue(result["restyled"])

    def test_unique_instance_set_comparison_rejects_duplicates(self):
        result = json.loads(run_node(
            "const {sameUniqueStringSet}=require('./scripts/pipeline_module_picker_smoke.js');"
            "process.stdout.write(JSON.stringify({"
            "same:sameUniqueStringSet(['a','b'],['b','a']),"
            "duplicateActual:sameUniqueStringSet(['a','a'],['a','b']),"
            "duplicateExpected:sameUniqueStringSet(['a','b'],['a','a'])}));"
        ))
        self.assertTrue(result["same"])
        self.assertFalse(result["duplicateActual"])
        self.assertFalse(result["duplicateExpected"])

    def test_smoke_owns_no_pipeline_identity_and_cleans_only_owned_session(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("tlm1d02", source.lower())
        self.assertIn("TRADE_PIPELINE_ID", source)
        self.assertIn("if (!session?.owned || !session.token) return", source)
        self.assertIn(
            "} finally {\n"
            "    try {\n"
            "      if (browser) await browser.close();\n"
            "    } finally {\n"
            "      deleteOwnedSession(session);\n"
            "    }\n"
            "  }",
            source,
        )
        self.assertIn("if (require.main === module)", source)
        self.assertIn('"--no-proxy-server"', source)

    def test_picker_assertions_bind_current_exact_keys_and_catalog_folders(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("window.TradeVersionSelection.currentRows", source)
        self.assertIn('String(candidate.versionKey || "") === definition.key', source)
        self.assertIn("optionPath === row.catalogPath", source)
        self.assertIn("row.title === row.expectedTitle", source)
        self.assertIn("row.subtitle === row.expectedSubtitle", source)
        self.assertIn("row.buttonRect.height <= CONTRACT.maxMenuItemHeight", source)
        self.assertIn('audit.emptyTrigger.title === ""', source)
        self.assertIn('Boolean(audit.emptyTrigger.ariaLabel)', source)
        self.assertIn("audit.placeholderMenuLabels.length === 0", source)
        self.assertIn("audit.rootMenuItemLabels.length === 0", source)
        self.assertIn("Pipeline geometry or computed styles changed after visiting Signal", source)

    def test_stage_assertions_bind_card_modes_to_exact_instances_and_boundaries(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/api/pipelines/${encodeURIComponent(expectedPipelineId)}", source)
        self.assertIn("audit.emptyPickers.every", source)
        self.assertIn("audit.renderedInstanceIds, expectedValidInstanceIds", source)
        self.assertIn("card.mode === CONTRACT.stageCardModes.singleModule", source)
        self.assertIn("card.mode === CONTRACT.stageCardModes.singleSlot", source)
        self.assertIn("card.mode === CONTRACT.stageCardModes.container", source)
        self.assertIn("card.tagAreaCount === 0 && card.tags.length === 0", source)
        self.assertIn("card.pickerCount === 0 && card.loadButtonCount === 0", source)
        self.assertIn("card.singleModule.identity.instanceId === expected.instanceId", source)
        self.assertIn("card.singleModule.identity.moduleId === expected.exactIdentity.moduleId", source)
        self.assertIn("card.singleModule.identity.version === expected.exactIdentity.version", source)
        self.assertIn("card.singleModule.identityText === expected.visibleIdentityText", source)
        self.assertIn("card.singleModule.identityTitle === expected.exactIdentityText", source)
        self.assertIn("card.singleModule.configure.count === 1", source)
        self.assertIn('card.singleModule.configure.text === "Configure"', source)
        self.assertIn("card.singleModule.inlineRemoveCount === 0", source)
        self.assertIn("sameUniqueStringSet(card.tagInstanceIds, audit.validDraftReferences[stage])", source)
        self.assertIn('card.tagAreaStyle?.borderTopWidth === "1px"', source)
        self.assertIn('card.tagAreaStyle?.borderTopStyle === "solid"', source)
        self.assertIn("lightNeutralBorder(card.tagAreaStyle?.borderTopColor)", source)
        self.assertIn("tag.name === expected.expectedName", source)
        self.assertIn("tag.configureStage === tag.stage", source)
        self.assertIn("tag.title.includes(expected.exactIdentityText)", source)
        self.assertIn("tag.ariaLabel.includes(expected.exactIdentityText)", source)
        self.assertIn("tag.rect.height - CONTRACT.stageTagHeight", source)
        self.assertIn("card.rect.width - CONTRACT.stageCardWidth", source)
        self.assertIn("card.trailingGap <= CONTRACT.stageTrailingGapMax", source)
        self.assertIn("new Set(CONTRACT.pipelineStages", source)
        self.assertIn('visibleActions.every((label) => label === "Load")', source)
        self.assertIn("audit.signal.summaryText === audit.signal.expectedSummaryText", source)
        self.assertIn(
            "signal.nodeCount === expectedSignal.expectedRenderedGraphNodes",
            source,
        )
        self.assertIn("trade-pipeline-stage-card-modes.png", source)

    def test_configure_dialog_asserts_compact_input_and_output_schema_hints(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("inputSchemaHints", source)
        self.assertIn("outputSchemaHints", source)
        self.assertIn("const conciseSchemaHint", source)
        self.assertIn("/^[^{}]+ · (Required|Optional)$/.test(hint)", source)
        self.assertIn("!/properties/i.test(hint)", source)
        self.assertIn(
            "Configure input hints are not concise type + Required/Optional labels",
            source,
        )
        self.assertIn(
            "Configure output hints are not concise type + Required/Optional labels",
            source,
        )


if __name__ == "__main__":
    unittest.main()
