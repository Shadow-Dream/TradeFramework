import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backtest_pipeline_config_override_smoke.js"


def run_node(source: str) -> str:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is required")
    return subprocess.check_output([node, "-e", source], cwd=ROOT, text=True)


class BacktestPipelineConfigOverrideSmokeContractTests(unittest.TestCase):
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

    def test_contract_names_exact_resource_module_and_build_boundaries(self):
        contract = json.loads(
            run_node(
                "const {CONTRACT}=require('./scripts/"
                "backtest_pipeline_config_override_smoke.js');"
                "process.stdout.write(JSON.stringify(CONTRACT));"
            )
        )
        self.assertEqual(contract["route"], "/backtests")
        self.assertEqual(contract["pipelineId"], "tlm1d02")
        self.assertEqual(contract["resourceScope"], "resource")
        self.assertEqual(contract["resourceInstanceId"], "resource.config")
        self.assertEqual(contract["moduleScope"], "module")
        self.assertEqual(contract["scalarModuleInstanceId"], "tlm.market")
        self.assertEqual(contract["scalarField"], "recessCloseHour")
        self.assertEqual(contract["cacheKey"], "trade.backtest.build.v2")
        self.assertEqual(
            contract["preparePath"], "/api/backtest-submissions/prepare"
        )
        self.assertEqual(contract["submissionPath"], "/api/backtests")
        self.assertEqual(
            contract["configLayers"],
            ["configOverride", "moduleConfigOverrides"],
        )

    def test_smoke_exposes_resource_first_and_checks_whitelist_blacklist(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('initial.heading === "Resource + Modules"', source)
        self.assertIn("initial.count === moduleIds.length + 1", source)
        self.assertIn("initial.instances[0]?.scope === CONTRACT.resourceScope", source)
        self.assertIn(
            "initial.instances[0]?.instanceId === CONTRACT.resourceInstanceId",
            source,
        )
        self.assertIn("setPipelineWhitelist(page, desiredWhitelist)", source)
        self.assertIn('[data-config-json-editor]', source)
        self.assertIn("initial.whitelist.length > 1", source)
        self.assertIn("Array.isArray(initial.blacklist)", source)

    def test_smoke_asserts_two_layer_apply_reopen_and_cache_round_trip(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("const expectedDraft = {", source)
        self.assertIn("configOverride: { observationInput: { whitelist", source)
        self.assertIn("moduleConfigOverrides:", source)
        self.assertIn("prospectiveRequest.pipeline.configOverride", source)
        self.assertIn("prospectiveRequest.pipeline.moduleConfigOverrides", source)
        self.assertGreaterEqual(source.count("auditReopenedConfig(page"), 2)
        self.assertIn("build.cache?.request", source)
        self.assertIn("await page.reload", source)
        self.assertIn("cacheRoundTrip.pipelineConfigOverride", source)
        self.assertIn("cacheRoundTrip.pipelineModuleConfigOverrides", source)

    def test_smoke_allows_only_public_build_and_never_runs(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'method === "POST" && path === CONTRACT.preparePath', source
        )
        self.assertIn(
            'method === "POST" && path === CONTRACT.submissionPath', source
        )
        self.assertIn('request.abort("blockedbyclient")', source)
        self.assertEqual(source.count('page.click("#runBacktestBtn")'), 1)
        self.assertIn("prepareRequests.length === 1", source)
        self.assertIn("resourceWrites.length === 0", source)
        self.assertIn("submissionAttempts.length === 0", source)
        self.assertIn("pageErrors.length === 0", source)
        self.assertIn("failedResponses.length === 0", source)

    def test_smoke_proves_exact_pipeline_resource_is_immutable(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("getExactPipeline(page, exact)", source)
        self.assertIn("before.body.definition?.contentDigest", source)
        self.assertIn("isDeepStrictEqual(before.body.definition, after.body.definition)", source)
        self.assertIn("if (!session?.owned || !session.token) return", source)
        self.assertIn("deleteOwnedSession(session)", source)
        self.assertIn("if (require.main === module)", source)
        self.assertIn('"--no-proxy-server"', source)

    def test_small_pure_helpers_are_deterministic(self):
        result = json.loads(
            run_node(
                "const {rotated,nextScalar}=require('./scripts/"
                "backtest_pipeline_config_override_smoke.js');"
                "process.stdout.write(JSON.stringify({"
                "rotated:rotated(['a','b','c']),"
                "next:nextScalar({value:'17',min:'0',max:'23'})}));"
            )
        )
        self.assertEqual(result["rotated"], ["b", "c", "a"])
        self.assertEqual(result["next"], 18)


if __name__ == "__main__":
    unittest.main()
