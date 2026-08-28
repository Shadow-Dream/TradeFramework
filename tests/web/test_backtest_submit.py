#!/usr/bin/env python3
"""Static browser contracts for the Backtest submission boundary."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
MODULE_FORMS_SOURCE = (ROOT / "web" / "module_forms.js").read_text(
    encoding="utf-8"
)
BROWSER_SOURCE = (ROOT / "web_src" / "trade_resource_browser.jsx").read_text(encoding="utf-8")


class BacktestSubmissionBoundaryTests(unittest.TestCase):
    def test_click_routes_build_then_run_without_automatic_preflight(self):
        handler = APP_SOURCE.split(
            '$("runBacktestBtn").addEventListener("click", () => {', 1
        )[1].split('$("chainDataset")', 1)[0]

        self.assertNotIn("postJson(", handler)
        self.assertIn("backtestCachedBuildMatches()", handler)
        self.assertIn("backtestPreparedTokenIsUsable()", handler)
        self.assertIn("preparedRequestFingerprint === requestFingerprint", handler)
        self.assertIn("submitPreparedBacktest();", handler)
        self.assertIn("buildBacktestSubmission({ runAfterBuild: true });", handler)
        self.assertIn("buildBacktestSubmission();", handler)

    def test_run_uses_only_the_prepared_submission_boundary(self):
        submit = APP_SOURCE.split(
            "async function submitPreparedBacktest() {", 1
        )[1].split('$("runBacktestBtn")', 1)[0]
        pending = APP_SOURCE.split(
            "function setBacktestSubmissionPending(pending) {", 1
        )[1].split("function backtestRequestFingerprint", 1)[0]
        sync = APP_SOURCE.split(
            "function syncBacktestRunState() {", 1
        )[1].split("function currentResultBacktestId()", 1)[0]

        self.assertEqual(submit.count('postJson("/api/backtests", {'), 1)
        self.assertNotIn("/api/backtest-submissions/prepare", submit)
        self.assertIn("preparedSubmissionToken,", submit)
        self.assertIn(
            'backtestEntryState.compositionValidation !== "valid"', submit
        )
        self.assertIn("preparedRequestFingerprint !== requestFingerprint", submit)
        self.assertIn("setBacktestSubmissionPending(true);", submit)
        self.assertIn("setBacktestSubmissionPending(false);", submit)
        self.assertIn('chain.inert = active', pending)
        self.assertIn('button.textContent = "Submitting…"', sync)
        self.assertIn('label = "Checking…"', sync)
        self.assertIn('label = "Build"', sync)
        self.assertIn('label = "Run Backtest"', sync)
        self.assertIn('button.classList.add("button-loading")', sync)
        self.assertIn(
            'id="runBacktestBtn" class="backtest-submit-action" type="button" disabled',
            HTML_SOURCE,
        )

    def test_dataset_is_one_business_resource_with_internal_evidence(self):
        self.assertNotIn("backtestDatasetVersion", HTML_SOURCE)
        self.assertNotIn("backtestDatasetVersion", APP_SOURCE)
        self.assertIn("function selectedBacktestDatasetEvidence()", APP_SOURCE)
        self.assertIn("dataset.latestVersionId", APP_SOURCE)
        self.assertIn("evidence locked automatically", APP_SOURCE)
        self.assertIn(
            'sourceRepository === "datasets" || record.version == null ? []',
            BROWSER_SOURCE,
        )

    def test_build_is_explicit_and_bound_to_the_exact_configuration(self):
        build = APP_SOURCE.split(
            "async function buildBacktestSubmission({ runAfterBuild = false } = {}) {", 1
        )[1].split("function renderBacktestChain() {", 1)[0]

        self.assertEqual(
            build.count('postJson("/api/backtest-submissions/prepare", request)'),
            1,
        )
        self.assertNotIn("setTimeout", build)
        self.assertNotIn("/api/backtest-compositions/validate", build)
        self.assertIn("result.preparedSubmissionToken", build)
        self.assertIn("currentFingerprint !== requestFingerprint", build)
        self.assertIn("preparedRequestFingerprint = prepared", build)
        self.assertIn("result.buildCacheExpiresInSeconds", build)
        self.assertIn("result.cacheHit", build)
        self.assertIn("if (runAfterBuild) await submitPreparedBacktest();", build)
        persisted = APP_SOURCE.split(
            "function persistBacktestBuildCache() {", 1
        )[1].split("function clearPersistedBacktestBuildCache()", 1)[0]
        self.assertIn("sessionStorage.setItem", persisted)
        self.assertIn("requestFingerprint", persisted)
        self.assertIn("requestDigest", persisted)
        self.assertIn("request: buildBacktestCompositionRequest()", persisted)
        self.assertNotIn("preparedSubmissionToken", persisted)
        restore = APP_SOURCE.split(
            "function restoreBacktestControlsFromBuildCache() {", 1
        )[1].split("function backtestCachedBuildMatches", 1)[0]
        self.assertIn("backtestEntryState.samplerParameters", restore)
        self.assertIn("backtestEntryState.pipelineVersion", restore)

    def test_configuration_changes_invalidate_a_completed_build(self):
        invalidation = APP_SOURCE.split(
            "function invalidateBacktestBuild(message = \"\") {", 1
        )[1].split("async function buildBacktestSubmission(", 1)[0]
        controls = APP_SOURCE.split(
            '$("backtestDataset").addEventListener("change", () => {', 1
        )[1].split('$("showArchivedBacktestsBtn")', 1)[0]

        self.assertIn("++backtestEntryState.compositionSequence;", invalidation)
        self.assertIn('preparedSubmissionToken = ""', invalidation)
        self.assertIn('preparedRequestDigest = ""', invalidation)
        self.assertIn('preparedRequestFingerprint = ""', invalidation)
        self.assertIn('compositionValidation = "build"', invalidation)
        self.assertGreaterEqual(controls.count("invalidateBacktestBuild("), 6)
        self.assertNotIn("/api/backtest-submissions/prepare", controls)
        self.assertNotIn("scheduleBacktestSubmissionPreparation", APP_SOURCE)
        self.assertGreaterEqual(
            APP_SOURCE.count("Pipeline modules changed · Build again before running"),
            4,
        )
        self.assertIn("Environment modules changed · Build again before running", APP_SOURCE)
        self.assertIn("Analysis modules changed · Build again before running", APP_SOURCE)

    def test_every_executable_resource_exposes_schema_and_json_configuration(self):
        for resource in ("Sampler", "Pipeline", "Environment", "Analysis"):
            self.assertIn(f'id="configureBacktest{resource}"', HTML_SOURCE)
        for field_id in (
            "backtestConfigInstanceList",
            "backtestConfigFields",
            "backtestConfigJson",
            "syncBacktestConfigJsonBtn",
            "resetBacktestConfigBtn",
        ):
            self.assertIn(f'id="{field_id}"', HTML_SOURCE)
        self.assertIn("function openBacktestConfig(resource)", APP_SOURCE)
        self.assertIn("ensureBacktestConfigModuleContracts(resource)", APP_SOURCE)
        self.assertIn('pipeline: ["pipelineModules", "/api/modules?limit=500"]', APP_SOURCE)
        self.assertIn('environment: ["environmentModules", "/api/environment-modules?limit=500"]', APP_SOURCE)
        self.assertIn('analysis: ["analysisModules", "/api/analysis-modules?limit=500"]', APP_SOURCE)
        self.assertIn("state.backtestPipelineDefinitions[key]", APP_SOURCE)
        self.assertIn("/api/pipelines/${encodeURIComponent(pipelineId)}/versions/${encodeURIComponent(version)}", APP_SOURCE)
        request_builder = APP_SOURCE.split(
            "function buildBacktestCompositionRequest() {", 1
        )[1].split("function renderBacktestCompositionStatus()", 1)[0]
        self.assertEqual(request_builder.count("configOverride:"), 1)
        self.assertEqual(request_builder.count("moduleConfigOverrides:"), 3)
        self.assertIn("samplerParameters", request_builder)
        apply_handler = APP_SOURCE.split(
            '$("applyBacktestSamplerConfigBtn")?.addEventListener("click", () => {',
            1,
        )[1].split('$("showArchivedBacktestsBtn")', 1)[0]
        self.assertIn("activeBacktestConfigJsonDirty", apply_handler)
        self.assertIn("readBacktestConfigJsonDraft()", apply_handler)
        self.assertIn("commitBacktestConfigFields()", apply_handler)
        self.assertIn("validateBacktestConfigDraft(activeBacktestConfigResource, value)", apply_handler)
        self.assertIn("setBacktestConfigValue(activeBacktestConfigResource, value)", apply_handler)

    def test_graph_config_editor_separates_resource_and_module_overrides(self):
        sparse = APP_SOURCE.split(
            "function sparseBacktestConfig(base, effective) {", 1
        )[1].split("function backtestConfigValue(resource)", 1)[0]
        commit = APP_SOURCE.split(
            "function commitBacktestConfigFields() {", 1
        )[1].split("function readBacktestConfigJsonDraft()", 1)[0]
        validation = APP_SOURCE.split(
            "function validateBacktestConfigDraft(resource, value) {", 1
        )[1].split("function setBacktestConfigLoading", 1)[0]

        self.assertIn("backtestConfigEqual(base, effective)", sparse)
        self.assertIn("sparseBacktestConfig(base[name], value)", sparse)
        self.assertIn("sparseBacktestConfig(descriptor.config, effective)", commit)
        self.assertIn("next.configOverride = empty ? {} : sparse", commit)
        self.assertIn(
            "delete next.moduleConfigOverrides?.[descriptor.instanceId]", commit
        )
        self.assertIn(
            "next.moduleConfigOverrides[descriptor.instanceId] = sparse", commit
        )
        self.assertIn('new Set(["configOverride", "moduleConfigOverrides"])', validation)
        self.assertIn('new Set(["moduleConfigOverrides"])', validation)
        self.assertIn("Unknown ${resource} Module instance", validation)
        self.assertIn("mergeBacktestConfig(descriptor.config, override)", validation)
        self.assertNotIn("initialCash", APP_SOURCE)
        self.assertNotIn("riskFreeRate", APP_SOURCE)
        self.assertNotIn("atrLen", APP_SOURCE)
        self.assertIn(".backtest-config-workbench {", CSS_SOURCE)
        self.assertIn("grid-template-columns: 250px minmax(360px, 1fr) 340px;", CSS_SOURCE)

    def test_unknown_sampler_length_is_an_indeterminate_counting_phase(self):
        renderer = APP_SOURCE.split(
            "function renderBacktestJobs() {", 1
        )[1].split("function scheduleBacktestJobPoll", 1)[0]

        self.assertIn('job.phase === "counting"', renderer)
        self.assertIn("Counting exact Sampler cycles", renderer)
        self.assertIn('const progressValue = job.status === "completed" || total > 0', renderer)
        self.assertIn('max="100"${progressValue}', renderer)
        self.assertNotIn(': "Preparing Backtest");', renderer)

    def test_backtest_and_pipeline_share_the_canvas_toolbar(self):
        self.assertEqual(HTML_SOURCE.count('class="canvas-toolbar '), 2)
        self.assertIn(
            'class="canvas-toolbar pipeline-canvas-toolbar"', HTML_SOURCE
        )
        self.assertIn(
            'class="canvas-toolbar backtest-canvas-toolbar"', HTML_SOURCE
        )
        backtest_graph = HTML_SOURCE.split(
            'id="backtestChain"', 1
        )[1].split('class="backtest-graph-canvas"', 1)[0]
        self.assertIn('id="backtestArrangeBtn"', backtest_graph)
        self.assertIn('id="backtestFitBtn"', backtest_graph)
        self.assertIn('id="backtestFullscreenBtn"', backtest_graph)

    def test_observation_filters_use_explicit_add_edit_and_batch_actions(self):
        self.assertNotIn('id="pipelineObservationWhitelist" rows=', HTML_SOURCE)
        self.assertNotIn('id="pipelineObservationBlacklist" rows=', HTML_SOURCE)
        self.assertIn('id="pipelineObservationWhitelistEditor"', HTML_SOURCE)
        self.assertIn('id="pipelineObservationBlacklistEditor"', HTML_SOURCE)
        self.assertNotIn('id="pipelineObservationWhitelistInput"', HTML_SOURCE)
        self.assertNotIn('id="pipelineObservationBlacklistInput"', HTML_SOURCE)
        self.assertIn('id="pipelineObservationWhitelistAddBtn"', HTML_SOURCE)
        self.assertIn('id="pipelineObservationBlacklistBatchBtn"', HTML_SOURCE)
        self.assertIn('id="pipelineObservationBatchDialog"', HTML_SOURCE)
        editor = APP_SOURCE.split(
            "function bindObservationEditor(fieldId) {", 1
        )[1].split("function bindObservationBatchDialog()", 1)[0]
        inline_editor = APP_SOURCE.split(
            "function beginObservationEntryEdit(fieldId, originalEntry = null) {", 1
        )[1].split("let pendingObservationBatchFieldId", 1)[0]
        batch = APP_SOURCE.split(
            "function observationBatchCandidate(fieldId, source) {", 1
        )[1].split("function bindObservationEditor(fieldId)", 1)[0]
        self.assertIn("beginObservationEntryEdit(fieldId)", editor)
        self.assertNotIn('event.key === "Backspace"', editor)
        self.assertNotIn('addEventListener("paste"', editor)
        self.assertIn('event.key !== "Enter"', inline_editor)
        self.assertIn("compositionJustEnded", inline_editor)
        self.assertIn("event.isComposing", inline_editor)
        self.assertIn("event.keyCode === 229", inline_editor)
        self.assertIn("observationPathError(value)", inline_editor)
        self.assertIn("already exists", inline_editor)
        self.assertIn("firstLineByValue", batch)
        self.assertIn("duplicates line", batch)
        self.assertIn("observationInputCandidateError", batch)
        self.assertIn("data-remove-observation-entry", APP_SOURCE)
        observation_styles = CSS_SOURCE.split(
            ".pipeline-observation-input {", 1
        )[1].split(".pipeline-version-footer {", 1)[0]
        self.assertIn(".observation-list-head {\n  min-height: 68px;", observation_styles)
        self.assertIn(".observation-entry-list {\n  height: 176px;", observation_styles)
        self.assertNotIn("min-height: 92px", observation_styles)
        self.assertNotIn("max-height: 176px", observation_styles)

    def test_pipeline_stage_cards_separate_single_modules_from_containers(self):
        inventory = APP_SOURCE.split(
            "function pipelineStageInventory(stage, kind, draft = state.pipelineDraft || {}) {",
            1,
        )[1].split("function parsePipelineAlphaGraphValue", 1)[0]
        renderer = APP_SOURCE.split(
            "function renderPipelineBuilder() {", 1
        )[1].split("function buildPipelinePayload()", 1)[0]
        single_markup = APP_SOURCE.split(
            "function pipelineSingleStageModuleMarkup(", 1
        )[1].split("function renderPipelineBuilder()", 1)[0]
        healthy_tag_markup = APP_SOURCE.split(
            "function pipelineStageModuleTagsMarkup(", 1
        )[1].split("const issueTags =", 1)[0]

        # The compact cards describe Modules actually loaded by a stage. An
        # unreferenced instance may be diagnosed, but must never be promoted to
        # a loaded card item merely because its kind happens to match.
        self.assertIn("const instances = draft?.instances || {};", inventory)
        self.assertIn("pipelineStageReferenceIds(stage, draft)", inventory)
        self.assertIn("pipelineInstanceOwnershipIndex(draft)", inventory)
        self.assertIn("referenceIssues", inventory)
        self.assertIn('status: "missing-instance"', inventory)
        self.assertIn('status: "kind-mismatch"', inventory)
        self.assertNotIn("const modules = Object.entries(instances)", inventory)
        self.assertIn("const modules = [];", inventory)
        self.assertIn("[...new Set(references)].forEach((instanceId) => {", inventory)
        self.assertIn("modules.push({", inventory)
        self.assertLess(inventory.index("if (!instance)"), inventory.index("modules.push({"))
        self.assertLess(
            inventory.index("if (instance.kind !== kind)"),
            inventory.index("modules.push({"),
        )

        self.assertIn(
            "pipelineStageInventory(stage, kind, state.pipelineDraft)", renderer
        )
        self.assertTrue(
            "dataset.stageCardMode" in renderer
            or "data-stage-card-mode" in renderer,
            "Every outer stage card must expose its semantic display mode",
        )
        for mode in ("container", "single-module", "single-slot"):
            self.assertIn(f'"{mode}"', renderer)

        # Signal and Constraint are real containers. Their loaded Modules live
        # in one bounded tag list; a single-stage Module must not reuse it.
        self.assertIn("pipeline-stage-container", renderer)
        self.assertIn("pipeline-stage-tag-list", renderer)
        self.assertRegex(
            renderer,
            r'class="[^"]*(?:pipeline-stage-tag-list[^"]*loaded-tags|'
            r'loaded-tags[^"]*pipeline-stage-tag-list)[^"]*"',
        )
        self.assertIn('stage === "signal"', renderer)
        self.assertIn("MULTI_STAGE.has(stage)", renderer)
        self.assertIn(
            'data-configure-stage-module="${escapeHtml(stage)}"',
            healthy_tag_markup,
        )
        self.assertNotIn("data-unload-stage", healthy_tag_markup)

        # Universe/Target resolve either to one Module card or to one loading
        # slot. Loaded single stages expose the Module identity directly; the
        # picker and Load action belong exclusively to the empty/error slot.
        self.assertIn("pipeline-single-module-card", renderer)
        self.assertIn("group.dataset.singleStageModule = singleModule.instanceId", renderer)
        self.assertIn("pipeline-single-stage-slot", renderer)
        single_selection = renderer.split("const singleModule =", 1)[1].split(";", 1)[0]
        self.assertTrue(
            'status === "loaded"' in single_selection
            or "inventory.loadedCount === 1" in single_selection,
            "An unavailable or invalid single-stage reference must render as a slot",
        )
        self.assertNotIn("loaded-tags", single_markup)
        self.assertNotIn("data-load-stage=", single_markup)
        self.assertNotIn("data-load-stage-button", single_markup)
        self.assertIn(
            'data-configure-stage-module="${escapeHtml(stage)}"',
            single_markup,
        )
        self.assertIn(">Configure</button>", single_markup)
        self.assertNotIn("data-unload-stage", single_markup)
        self.assertNotIn(">Remove</button>", single_markup)
        self.assertIn('data-load-stage="${stage}"', renderer)
        self.assertIn(
            '<button data-load-stage-button="${stage}" type="button">Load</button>',
            renderer,
        )
        self.assertNotIn("pipeline-stage-module-row", renderer)

    def test_pipeline_instance_dialog_separates_load_and_configure_modes(self):
        for field_id in (
            "moduleLoadDialogMeta",
            "removeConfiguredModuleBtn",
            "cancelModuleLoadBtn",
            "confirmModuleLoadBtn",
        ):
            self.assertIn(f'id="{field_id}"', HTML_SOURCE)

        tag_markup = APP_SOURCE.split(
            "function pipelineStageModuleTagsMarkup(", 1
        )[1].split("function pipelineStageIssueRowsMarkup", 1)[0]
        single_markup = APP_SOURCE.split(
            "function pipelineSingleStageModuleMarkup(", 1
        )[1].split("function renderPipelineBuilder()", 1)[0]
        renderer = APP_SOURCE.split(
            "function renderPipelineBuilder() {", 1
        )[1].split("function buildPipelinePayload()", 1)[0]

        # Healthy container tags and the sole action on a loaded single-stage
        # card both configure the exact existing instance. Removal is a dialog
        # action, not an overloaded tag click or a second crowded card action.
        self.assertIn("data-configure-stage-module", tag_markup)
        self.assertIn("data-configure-stage-module", single_markup)
        self.assertIn(
            'querySelectorAll("[data-configure-stage-module]")', renderer
        )
        self.assertIn("dataset.configureStageModule", renderer)
        self.assertIn("dataset.instance", renderer)

        # One dialog supports both operations, but its visible contract must be
        # mode-specific: creation is Cancel/Load, editing is Remove/Cancel/Apply.
        self.assertIn('textContent = "Load"', APP_SOURCE)
        self.assertIn('textContent = "Apply"', APP_SOURCE)
        self.assertIn("removeConfiguredModuleBtn", APP_SOURCE)
        self.assertIn("moduleLoadDialogMeta", APP_SOURCE)

    def test_pipeline_instance_configure_preserves_identity_and_wiring(self):
        self.assertIn("function openModuleConfigureDialog(", APP_SOURCE)
        configure_open = APP_SOURCE.split(
            "function openModuleConfigureDialog(", 1
        )[1].split("function ", 1)[0]
        dialog_open = APP_SOURCE.split(
            "function openModuleLoadDialog(", 1
        )[1].split("function openModuleConfigureDialog", 1)[0]
        confirm = APP_SOURCE.split(
            "function confirmModuleLoad() {", 1
        )[1].split("function openUnloadDialog", 1)[0]

        # Configure resolves the exact Draft instance and its immutable Module
        # definition. Existing values, not schema/default-port values, seed the
        # editor. Outputs are visible but locked because changing DataKeys here
        # would silently break graph wiring.
        self.assertIn("pipelineStageInventory(", configure_open)
        self.assertIn('candidate.status === "loaded"', configure_open)
        self.assertIn("entry.definition", configure_open)
        self.assertIn('{ mode: "edit", instance: entry.instance }', configure_open)
        self.assertIn("instance.config || {}", dialog_open)
        self.assertIn("instance.inputs || {}", dialog_open)
        self.assertIn("instance.outputs || {}", dialog_open)
        self.assertIn("{ autoSelectSingle: !editing }", dialog_open)
        self.assertIn(
            "Object.prototype.hasOwnProperty.call(instance.inputs || {}, name)",
            dialog_open,
        )
        self.assertIn("editing ? {} : defaultPortOutputs(module)", dialog_open)
        self.assertIn("input.readOnly = editing", dialog_open)

        # Applying edits updates the same Draft record's configurable values.
        # It must not re-load, detach, replace, or rewrite Stage ownership.
        self.assertIn('const editing = mode === "edit"', confirm)
        edit_apply = confirm.split(
            "if (editing) {\n    const current", 1
        )[1].split("return true;", 1)[0]
        self.assertIn("const nextValues = { config, inputs, outputs };", edit_apply)
        self.assertIn("[instanceId]: { ...current, ...nextValues }", edit_apply)
        self.assertNotIn("loadStageInstance(", edit_apply)
        self.assertNotIn("state.pipelineDraft.stages", edit_apply)
        self.assertNotIn("alphaGraph.nodes", edit_apply)
        self.assertNotIn("detachPipelineInstanceFromDraft", confirm)

    def test_pipeline_module_dialog_uses_compact_port_schema_hints(self):
        input_definitions = APP_SOURCE.split(
            "function moduleDialogInputDefinitions(", 1
        )[1].split("function moduleDialogDataKeyOptions", 1)[0]
        dialog_open = APP_SOURCE.split(
            "function openModuleLoadDialog(", 1
        )[1].split("function openModuleConfigureDialog", 1)[0]
        output_renderer = MODULE_FORMS_SOURCE.split(
            "function renderPortFields(", 1
        )[1].split("function readPortFields", 1)[0]

        # A field hint is UI copy, not a JSON-schema dump. Both input and
        # output bindings expose only the short type and requirement status.
        self.assertNotIn("JSON.stringify", input_definitions)
        for source in (input_definitions, output_renderer):
            self.assertIn("schemaTypeLabel", source)
            self.assertIn('"Optional"', source)
            self.assertIn('"Required"', source)
        self.assertIn("behavior.compactHints", output_renderer)
        self.assertIn("{ compactHints: true }", dialog_open)

    def test_single_stage_replace_removes_displaced_instances_atomically(self):
        loader = APP_SOURCE.split(
            "function loadStageInstance(stage, instanceId) {", 1
        )[1].split("function detachPipelineInstanceFromDraft", 1)[0]
        detach = APP_SOURCE.split(
            "function detachPipelineInstanceFromDraft(instanceId) {", 1
        )[1].split("function unloadStageInstance", 1)[0]

        self.assertIn("if (MULTI_STAGE.has(stage))", loader)
        self.assertIn("const expectedKind = PIPELINE_STAGES.find", loader)
        self.assertIn("Object.entries(state.pipelineDraft.instances || {})", loader)
        self.assertIn(
            "candidateId !== instanceId && instance?.kind === expectedKind",
            loader,
        )
        self.assertIn("detachPipelineInstanceFromDraft(candidateId)", loader)
        self.assertIn("state.pipelineDraft.stages[stage] = [instanceId];", loader)
        self.assertLess(
            loader.index("detachPipelineInstanceFromDraft(candidateId)"),
            loader.index("state.pipelineDraft.stages[stage] = [instanceId];"),
        )
        self.assertIn("PIPELINE_MODULE_STAGES.forEach", detach)
        self.assertIn("draft.alphaGraph.nodes = previousNodes.filter", detach)
        self.assertIn("delete draft.instances[instanceId]", detach)

    def test_pipeline_module_placeholder_is_not_a_visible_repository_choice(self):
        renderer = APP_SOURCE.split(
            "function renderPipelineBuilder() {", 1
        )[1].split("function buildPipelinePayload()", 1)[0]
        row_projection = APP_SOURCE.split(
            "function hierarchicalOptionRows(select) {", 1
        )[1].split("function enhanceHierarchicalRepositorySelect", 1)[0]
        enhancer = APP_SOURCE.split(
            "function enhanceHierarchicalRepositorySelect(select) {", 1
        )[1].split("function selectMultipleValues", 1)[0]

        self.assertIn(
            '<option value="" data-hierarchy-placeholder="true"></option>',
            renderer,
        )
        self.assertNotIn("Select module", renderer)
        self.assertIn(
            'placeholder: option.dataset.hierarchyPlaceholder === "true"',
            row_projection,
        )
        self.assertIn(
            "const choices = rows.filter((row) => !row.placeholder);", enhancer
        )
        self.assertIn(
            "const selected = choices.find((row) => row.value === select.value) || null;",
            enhancer,
        )
        self.assertIn("choices.forEach((row) => {", enhancer)
        self.assertIn(
            "'<span></span><small></small><b aria-hidden=\"true\">▾</b>'",
            enhancer,
        )
        self.assertNotIn(">Select module<", enhancer)

    def test_pipeline_container_tags_scroll_without_zooming_the_canvas(self):
        pipeline_renderer = APP_SOURCE.split(
            "function renderPipelineBuilder() {", 1
        )[1].split("function buildPipelinePayload()", 1)[0]
        pipeline_zoom = APP_SOURCE.split(
            "function zoomPipelineViewport(event) {", 1
        )[1].split("function bindPipelineViewportControls()", 1)[0]

        container_scroll_style = CSS_SOURCE.split(
            "body.route-pipeline-builder #pipelineComposerSection .loaded-tags-scroll {",
            1,
        )[1].split("}", 1)[0]

        self.assertIn("pipeline-stage-container", pipeline_renderer)
        self.assertIn("pipeline-stage-tag-list", pipeline_renderer)
        self.assertIn("loaded-tags-scroll", pipeline_renderer)
        self.assertIn(
            'event.target.closest(".loaded-tags-scroll")', pipeline_zoom
        )
        self.assertIn("max-height: 108px", container_scroll_style)
        self.assertIn("overflow-y: auto", container_scroll_style)
        self.assertIn("overscroll-behavior: contain", container_scroll_style)


if __name__ == "__main__":
    unittest.main()
