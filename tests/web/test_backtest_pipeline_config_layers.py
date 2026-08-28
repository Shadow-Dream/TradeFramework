#!/usr/bin/env python3
"""Static UI contracts for independent Backtest resource and Module config layers."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return APP_SOURCE.split(f"function {name}", 1)[1].split(
        f"function {next_name}", 1
    )[0]


class BacktestPipelineConfigLayerTests(unittest.TestCase):
    def test_backtest_state_keeps_resource_and_module_overrides_independent(self):
        state = APP_SOURCE.split("const backtestEntryState = {", 1)[1].split(
            "};\nwindow.__tradeBacktestEntryState", 1
        )[0]

        self.assertIn("pipelineConfigOverride", state)
        self.assertIn("pipelineModuleConfigOverrides", state)
        self.assertIn("environmentModuleConfigOverrides", state)
        self.assertIn("analysisModuleConfigOverrides", state)
        self.assertNotIn("environmentConfigOverride", state)
        self.assertNotIn("analysisConfigOverride", state)

    def test_pipeline_editor_exposes_resource_config_before_module_instances(self):
        schema = function_source(
            "pipelineBacktestConfigSchema() {",
            "inferSamplerParameterSchema(config = {}) {",
        )
        descriptors = function_source(
            "backtestConfigDescriptors(resource = activeBacktestConfigResource) {",
            "selectedBacktestConfigDescriptor() {",
        )
        renderer = function_source(
            "renderBacktestConfigInstanceList() {",
            "renderBacktestConfigFieldsHeader(descriptor) {",
        )

        self.assertIn('required: ["observationInput"]', schema)
        self.assertIn('required: ["whitelist", "blacklist"]', schema)
        self.assertIn("uniqueItems: true", schema)
        self.assertIn('instanceId: "resource.config"', descriptors)
        self.assertIn("definition.config", descriptors)
        self.assertLess(
            descriptors.index('instanceId: "resource.config"'),
            descriptors.index("definition.instances"),
        )
        self.assertIn('data-backtest-config-scope="${escapeHtml(descriptor.scope)}"', renderer)
        self.assertIn('scope: "resource"', descriptors)
        self.assertIn('scope: "module"', descriptors)

    def test_resource_config_selection_cannot_shadow_a_same_named_module(self):
        selected = function_source(
            "selectedBacktestConfigDescriptor() {",
            "validateBacktestConfigDraft(resource, value) {",
        )
        renderer = function_source(
            "renderBacktestConfigInstanceList() {",
            "renderBacktestConfigFieldsHeader(descriptor) {",
        )
        click = APP_SOURCE.split(
            '$("backtestConfigInstanceList")?.addEventListener("click", (event) => {',
            1,
        )[1].split(
            '$("backtestConfigInstanceSearch")?.addEventListener', 1
        )[0]

        # Module instance IDs are arbitrary safe path segments, so a Module is
        # allowed to be named ``resource.config``. Scope must therefore be part
        # of selection identity everywhere instead of relying on the display ID.
        for source in (selected, renderer, click):
            self.assertIn("activeBacktestConfigInstanceId", source)
            self.assertIn("activeBacktestConfigScope", source)
        self.assertIn("button.dataset.backtestConfigScope", click)
        self.assertIn("activeBacktestConfigScope = button.dataset.backtestConfigScope", click)

    def test_config_json_and_validation_preserve_both_named_layers(self):
        value = function_source(
            "backtestConfigValue(resource) {",
            "setBacktestConfigValue(resource, value) {",
        )
        setter = function_source(
            "setBacktestConfigValue(resource, value) {",
            "backtestConfigSelection(resource) {",
        )
        validation = function_source(
            "validateBacktestConfigDraft(resource, value) {",
            "setBacktestConfigLoading(loading) {",
        )

        for source in (value, setter, validation):
            self.assertIn("configOverride", source)
            self.assertIn("moduleConfigOverrides", source)
        self.assertIn('scope }) => scope === "resource"', validation)
        self.assertIn("descriptor?.config", validation)
        self.assertIn('scope }) => scope === "module"', validation)

    def test_build_payload_uses_clean_public_field_names(self):
        request = function_source(
            "buildBacktestCompositionRequest() {",
            "renderBacktestCompositionStatus() {",
        )
        pipeline = request.split("pipeline: {", 1)[1].split("},\n    datasetId", 1)[0]
        environment = request.split("environment: {", 1)[1].split("},\n    analysis", 1)[0]
        analysis = request.split("analysis: {", 1)[1].split("},\n  };", 1)[0]

        self.assertIn("configOverride:", pipeline)
        self.assertIn("moduleConfigOverrides:", pipeline)
        for graph in (environment, analysis):
            self.assertIn("moduleConfigOverrides:", graph)
            self.assertNotIn("configOverride:", graph)

    def test_agent_draft_and_build_cache_round_trip_every_layer(self):
        ui_document = function_source(
            "activeBacktestUiDocument() {",
            "activeVisualizationUiDocument() {",
        )
        restore = function_source(
            "restoreBacktestControlsFromBuildCache() {",
            "backtestCachedBuildMatches(request = buildBacktestCompositionRequest()) {",
        )

        self.assertIn("schemaVersion: 2", ui_document)
        self.assertIn("parsed.schemaVersion !== 2", ui_document)
        self.assertNotIn("schemaVersion: 1", ui_document)
        for source in (ui_document, restore):
            self.assertIn("pipeline.configOverride", source)
            self.assertIn("pipeline.moduleConfigOverrides", source)
            self.assertIn("environment.moduleConfigOverrides", source)
            self.assertIn("analysis.moduleConfigOverrides", source)
            self.assertIn("pipelineConfigOverride", source)
            self.assertIn("pipelineModuleConfigOverrides", source)
            self.assertIn("environmentModuleConfigOverrides", source)
            self.assertIn("analysisModuleConfigOverrides", source)

    def test_new_config_contract_does_not_restore_legacy_v1_cache(self):
        self.assertIn(
            'const BACKTEST_BUILD_CACHE_KEY = "trade.backtest.build.v2";',
            APP_SOURCE,
        )
        self.assertNotIn('"trade.backtest.build.v1"', APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
