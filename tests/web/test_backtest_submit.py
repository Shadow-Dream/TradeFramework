#!/usr/bin/env python3
"""Static browser contracts for the Backtest submission boundary."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
BROWSER_SOURCE = (ROOT / "web_src" / "trade_resource_browser.jsx").read_text(encoding="utf-8")


class BacktestSubmissionBoundaryTests(unittest.TestCase):
    def test_click_routes_build_then_run_without_automatic_preflight(self):
        handler = APP_SOURCE.split(
            '$("runBacktestBtn").addEventListener("click", () => {', 1
        )[1].split('$("resultTimezoneBtn")', 1)[0]

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
        self.assertIn('sourceRepository === "datasets" ? []', BROWSER_SOURCE)

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

    def test_signal_module_list_scrolls_without_zooming_the_canvas(self):
        pipeline_renderer = APP_SOURCE.split(
            "function renderPipelineBuilder() {", 1
        )[1].split("function buildPipelinePayload()", 1)[0]
        pipeline_zoom = APP_SOURCE.split(
            "function zoomPipelineViewport(event) {", 1
        )[1].split("function bindPipelineViewportControls()", 1)[0]

        self.assertIn('" loaded-tags-scroll"', pipeline_renderer)
        self.assertIn('event.target.closest(".loaded-tags-scroll")', pipeline_zoom)
        self.assertIn(".loaded-tags-scroll {", CSS_SOURCE)
        self.assertIn("max-height: 108px", CSS_SOURCE)
        self.assertIn("overflow-y: auto", CSS_SOURCE)


if __name__ == "__main__":
    unittest.main()
