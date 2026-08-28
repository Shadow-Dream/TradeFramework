#!/usr/bin/env python3
"""Static and pure checks for the no-save Pipeline stage browser smoke."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pipeline_stage_inventory_smoke.js"


def run_node(source: str) -> str:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is required")
    return subprocess.check_output([node, "-e", source], cwd=ROOT, text=True)


class PipelineStageInventorySmokeTests(unittest.TestCase):
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

    def test_ownership_audit_separates_orphan_missing_and_kind_mismatch(self):
        result = json.loads(run_node(
            "const {ownershipAudit}=require('./scripts/pipeline_stage_inventory_smoke.js');"
            "const target={instanceId:'target',kind:'Target'};"
            "const signal={instanceId:'signal',kind:'Signal'};"
            "const base={stages:{universe:[],target:['target'],constraint:[]},"
            "alphaGraph:{nodes:['signal']},instances:{target,signal}};"
            "const orphan=structuredClone(base);"
            "orphan.instances.orphan={instanceId:'orphan',kind:'Target'};"
            "const missing=structuredClone(base);"
            "missing.stages.target=['missing']; delete missing.instances.target;"
            "const mismatch=structuredClone(base);"
            "mismatch.alphaGraph.nodes=[]; mismatch.stages.target=['signal'];"
            "process.stdout.write(JSON.stringify({"
            "clean:ownershipAudit(base),orphan:ownershipAudit(orphan),"
            "missing:ownershipAudit(missing),mismatch:ownershipAudit(mismatch)}));"
        ))
        self.assertEqual(result["clean"]["orphanIds"], [])
        self.assertEqual(result["clean"]["missingReferences"], [])
        self.assertEqual(result["clean"]["kindMismatches"], [])
        self.assertEqual(result["orphan"]["orphanIds"], ["orphan"])
        self.assertEqual(
            result["missing"]["missingReferences"],
            [{"stage": "target", "instanceId": "missing"}],
        )
        self.assertEqual(
            result["mismatch"]["kindMismatches"],
            [{
                "stage": "target",
                "instanceId": "signal",
                "expected": "Target",
                "actual": "Signal",
            }],
        )

    def test_browser_contract_uses_single_card_slot_transition_and_no_save_gate(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(".pipeline-single-module-card[data-single-stage-module]", source)
        self.assertIn(".pipeline-single-module-identity[data-single-module-meta]", source)
        self.assertIn(".pipeline-single-stage-slot", source)
        self.assertIn("card?.dataset.stageCardMode === \"single-slot\"", source)
        self.assertIn("snapshot.mode === CONTRACT.stageCardModes.singleModule", source)
        self.assertIn("snapshot.mode === CONTRACT.stageCardModes.singleSlot", source)
        self.assertIn("snapshot.tagListCount === 0", source)
        self.assertIn("snapshot.pickerCount === 0", source)
        self.assertIn("snapshot.pickerCount === 1", source)
        self.assertIn('snapshot.loadAction === "Load"', source)
        self.assertIn("await page.click(\"#confirmUnloadBtn\")", source)
        self.assertIn("assertSingleStageSlot(emptyDom", source)
        self.assertIn("assertSingleModuleCard(dom", source)
        self.assertIn("missing.issueRows[0]?.status === \"missing-instance\"", source)
        self.assertIn("mismatch.issueRows[0]?.status === \"kind-mismatch\"", source)
        self.assertIn("await page.setRequestInterception(true)", source)
        self.assertIn("blockedPipelineMutations", source)
        self.assertIn("saved: false", source)


if __name__ == "__main__":
    unittest.main()
