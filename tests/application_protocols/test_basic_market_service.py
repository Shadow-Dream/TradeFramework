"""Application-layer materialization through ordinary Trade Engine resources."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from application_protocols.basic_workflow import market_data as market_data_module
from application_protocols.basic_workflow import market_service as market_service_module
from application_protocols.basic_workflow.manifest import (
    PROFILE_ID,
    PROTOCOL_ID,
    V3_PROFILE_ID,
    V3_PROTOCOL_VERSION,
)
from application_protocols.basic_workflow.market_data import (
    DEFAULT_PROVIDER_ID,
    EODHD_ADJUSTMENT_POLICY,
    EODHD_DEMO_PROVIDER_ID,
    EODHD_REVISION_POLICY,
    EodhdDemoSnapshotProvider,
)
from application_protocols.basic_workflow.market_service import (
    _bar_snapshot,
    market_state,
    open_chart,
    open_instrument,
    project_sample_result_cached,
    project_result_cached,
    run_snapshot_jobs,
    set_watchlist,
    sync_market,
)
from builtin_implementations import resources as builtin_resources
from engine.authority.dataset import verify_dataset_version_storage_authority
from engine.authority.sampler import verify_sampler_runtime_bundle_authority
from engine.control import database as engine_database
from engine.repository import (
    backtest_results,
    datasets,
    module_definitions,
    pipelines,
    samplers,
)
from engine.runtime.dataset import create_dataset_handle
from engine.runtime.sampler import create_verified_sampler_runtime
from engine.service import pipelines as pipeline_service
from engine.service import visualizations as visualization_service
from engine.service import sample_visualizations as sample_visualization_service
from engine.service.backtest_submissions import PreparedBacktestSubmissionStore


class _FixtureProvider:
    provider_id = DEFAULT_PROVIDER_ID

    def sync_catalog(self):
        return {
            "asOf": "2026-08-26T12:00:00Z",
            "instruments": [
                {
                    "instrumentId": "US-AAPL",
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                    "assetType": "stock",
                    "availablePeriods": ["day"],
                }
            ],
        }


    def download_bars(self, instrument, period):
        if instrument["instrumentId"] != "US-AAPL" or period != "day":
            raise AssertionError("The service changed the explicit instrument request.")
        return {
            "providerId": self.provider_id,
            "asOf": "2026-08-26T12:01:00Z",
            "period": "day",
            "bars": [
                {
                    "time": "2026-01-02T21:00:00Z",
                    "open": 100.0,
                    "close": 101.0,
                    "high": 102.0,
                    "low": 99.0,
                },
                {
                    "time": "2026-01-05T21:00:00Z",
                    "open": 101.0,
                    "close": 103.0,
                    "high": 104.0,
                    "low": 100.0,
                },
                {
                    "time": "2026-01-06T21:00:00Z",
                    "open": 103.0,
                    "close": 102.0,
                    "high": 105.0,
                    "low": 101.0,
                },
            ],
        }


class _MultiFixtureProvider(_FixtureProvider):
    def sync_catalog(self):
        base = super().sync_catalog()
        base["instruments"].extend([
            {
                "instrumentId": "US-MSFT",
                "symbol": "MSFT",
                "name": "Microsoft Corporation",
                "exchange": "NASDAQ",
                "currency": "USD",
                "assetType": "stock",
                "availablePeriods": ["day"],
            },
            {
                "instrumentId": "US-NVDA",
                "symbol": "NVDA",
                "name": "NVIDIA Corporation",
                "exchange": "NASDAQ",
                "currency": "USD",
                "assetType": "stock",
                "availablePeriods": ["day"],
            },
        ])
        return base


class _PreparedBoundaryJobManager:
    def __init__(self, store):
        self.store = store
        self.submissions = []
        self.jobs = {}

    def submit(self, request, *, prepared_submission_token, session_identity):
        frozen = self.store.consume(
            prepared_submission_token,
            request,
            session_identity=session_identity,
        )
        self.submissions.append(frozen)
        suffix = "RSTVWXYZ"[len(self.submissions) - 1]
        job = {
            "jobId": f"job_01M0SCSPWCMW192DPJ4B0PX99{suffix}",
            "status": "queued",
            "phase": "queued",
            "pipelineId": request["pipeline"]["pipelineId"],
            "datasetId": request["datasetId"],
            "submittedAt": "2026-08-26T12:02:00Z",
            "startedAt": None,
            "completedAt": None,
            "totalCycles": 0,
            "completedCycles": 0,
            "progress": 0.0,
            "backtestId": f"bt_01M0SCSPWCMW192DPJ4B0PX99{suffix}",
            "error": None,
            "snapshotHash": frozen["executionSnapshot"]["snapshotHash"],
        }
        self.jobs[job["jobId"]] = copy.deepcopy(job)
        return job

    def get(self, job_id):
        if job_id not in self.jobs:
            raise ValueError(f"Unknown Backtest job: {job_id}")
        return copy.deepcopy(self.jobs[job_id])


def _generated_eodhd_payload():
    rows = []
    current = date(2026, 7, 30)
    while current <= date(2026, 8, 26):
        if current.weekday() < 5:
            offset = len(rows)
            close = 200.5 + offset
            rows.append({
                "date": current.isoformat(),
                "open": 200.0 + offset,
                "high": 202.0 + offset,
                "low": 199.0 + offset,
                "close": close,
                "adjusted_close": close,
                "volume": 2_000_000 + offset * 1000,
            })
        current += timedelta(days=1)
    if len(rows) != 20:
        raise AssertionError("The generated EODHD fixture must contain twenty rows.")
    raw = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    return raw, rows


class BasicMarketServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        builtin_resources.install(self.config)
        self.provider = _FixtureProvider()
        self.providers = {self.provider.provider_id: self.provider}

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_watchlist_and_open_cross_canonical_engine_boundaries(self):
        self.assertEqual(
            market_state(self.config),
            {
                "protocolId": PROTOCOL_ID,
                "snapshot": None,
                "watchlist": [],
                "barSnapshots": [],
                "snapshotJobs": [],
            },
        )
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        snapshot = synced["snapshot"]
        self.assertRegex(
            snapshot["snapshotId"],
            r"^basic-market-catalog-[0-9a-f]{24}$",
        )
        self.assertEqual(snapshot["instrumentCount"], 1)
        self.assertEqual(
            [item["symbol"] for item in synced["watchlist"]],
            ["AAPL"],
        )
        self.assertNotIn("bars", json.dumps(synced))

        watched = set_watchlist(
            self.config,
            {
                "snapshotId": snapshot["snapshotId"],
                "instrumentIds": ["US-AAPL"],
            },
        )
        self.assertEqual(watched["watchlist"][0]["symbol"], "AAPL")
        self.assertEqual(market_state(self.config)["watchlist"], watched["watchlist"])

        occupied_pipeline = pipeline_service.archive_pipeline_if_changed(
            self.config,
            {
                "pipelineId": "basic-market-snapshot-day",
                "name": "User-owned pipeline with the former application name",
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": {},
                "stages": {},
                "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            },
        )["definition"]

        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            {
                "snapshotId": snapshot["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        self.assertEqual(opened["protocolId"], PROTOCOL_ID)
        self.assertEqual(opened["job"]["status"], "queued")
        self.assertEqual(
            opened["job"]["backtestId"],
            "bt_01M0SCSPWCMW192DPJ4B0PX99R",
        )
        self.assertEqual(len(jobs.submissions), 1)
        self.assertEqual(
            opened["prepared"]["snapshotHash"],
            jobs.submissions[0]["executionSnapshot"]["snapshotHash"],
        )

        materialization = opened["materialization"]
        expected_bar_projection = {
            "catalogSnapshotId": snapshot["snapshotId"],
            "providerId": opened["barSnapshot"]["providerId"],
            "instrumentId": "US-AAPL",
            "period": opened["barSnapshot"]["period"],
            "asOf": opened["barSnapshot"]["asOf"],
            "firstTime": opened["barSnapshot"]["firstTime"],
            "lastTime": opened["barSnapshot"]["lastTime"],
            "barCount": opened["barSnapshot"]["barCount"],
            "contentDigest": opened["barSnapshot"]["contentDigest"],
            "datasetId": materialization["dataset"]["datasetId"],
            "datasetVersionId": materialization["dataset"]["datasetVersionId"],
        }
        self.assertEqual(
            market_state(self.config)["barSnapshots"],
            [expected_bar_projection],
        )
        self.assertEqual(
            expected_bar_projection["lastTime"],
            "2026-01-06T21:00:00Z",
        )
        self.assertNotEqual(
            expected_bar_projection["lastTime"],
            expected_bar_projection["asOf"],
        )
        dataset = datasets.ensure_dataset_version(
            self.config,
            materialization["dataset"]["datasetId"],
            materialization["dataset"]["datasetVersionId"],
        )
        self.assertEqual(dataset["protocolId"], PROTOCOL_ID)
        pipeline = pipelines.load_pipeline_version(
            self.config,
            materialization["pipeline"]["pipelineId"],
            materialization["pipeline"]["version"],
        )
        self.assertEqual(pipeline["protocolId"], PROTOCOL_ID)
        self.assertEqual(
            materialization["pipeline"]["contentDigest"],
            pipeline["contentDigest"],
        )
        self.assertNotEqual(
            pipeline["pipelineId"], occupied_pipeline["pipelineId"]
        )
        self.assertEqual(
            pipelines.load_current_pipeline(
                self.config, occupied_pipeline["pipelineId"]
            ),
            occupied_pipeline,
        )

        save_request = materialization["visualizationSaveRequest"]
        self.assertEqual(save_request["expectedRevision"], 0)
        self.assertEqual(
            save_request["visualizationId"],
            visualization_service.current_visualization_id(
                opened["job"]["backtestId"]
            ),
        )
        candles = save_request["spec"]["panes"][0]["visualizers"]
        self.assertEqual(candles[0]["callback"], "ohlc.candles")
        self.assertEqual(
            candles[0]["params"]["dataKey"],
            "price.day.US-AAPL",
        )
        response_text = json.dumps(opened, sort_keys=True)
        self.assertNotIn('"bars"', response_text)
        self.assertNotIn('"open": 100', response_text)

        managed_pipeline = materialization["pipeline"]
        self.assertEqual(
            len(
                pipelines.pipeline_versions(
                    self.config,
                    managed_pipeline["pipelineId"],
                )
            ),
            2,
        )
        reopened = open_instrument(
            self.config,
            {
                "snapshotId": snapshot["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        self.assertEqual(reopened["materialization"]["pipeline"], managed_pipeline)
        self.assertEqual(reopened["job"]["backtestId"], opened["job"]["backtestId"])
        self.assertEqual(
            market_state(self.config)["barSnapshots"],
            [expected_bar_projection],
        )
        self.assertEqual(
            len(
                pipelines.pipeline_versions(
                    self.config,
                    managed_pipeline["pipelineId"],
                )
            ),
            2,
        )

        user_v3 = pipeline_service.rename_pipeline(
            self.config,
            managed_pipeline["pipelineId"],
            "User v3 must remain current",
        )["definition"]
        self.assertEqual(user_v3["version"], "3")
        reopened_after_user_edit = open_instrument(
            self.config,
            {
                "snapshotId": snapshot["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        self.assertEqual(
            reopened_after_user_edit["materialization"]["pipeline"],
            managed_pipeline,
        )
        self.assertEqual(
            pipelines.load_current_pipeline(
                self.config,
                managed_pipeline["pipelineId"],
            ),
            user_v3,
        )
        self.assertEqual(
            reopened_after_user_edit["job"]["backtestId"],
            opened["job"]["backtestId"],
        )
        self.assertEqual(len(jobs.submissions), 1)

    def test_eodhd_v3_fetch_publish_sampler_and_open_are_causal_and_token_free(self):
        raw, rows = _generated_eodhd_payload()
        calls = []

        def fetch_json(url, *, limit):
            calls.append((url, limit))
            return raw, rows

        provider = EodhdDemoSnapshotProvider(
            fetch_json=fetch_json,
            today=date(2026, 8, 26),
        )
        providers = {provider.provider_id: provider}
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        with mock.patch.object(
            market_data_module.engine_clock,
            "utc_now",
            return_value="2026-08-27T18:59:33Z",
        ):
            synced = sync_market(
                self.config,
                {"providerId": EODHD_DEMO_PROVIDER_ID},
                providers=providers,
            )
            opened = open_instrument(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentId": "US-TSLA",
                    "period": "day",
                },
                prepared_store=store,
                job_manager=jobs,
                session_identity="test-session",
                providers=providers,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(opened["materialization"]["sampler"]["samplerId"],
                         "basic-ohlcv-map-sampler")
        self.assertEqual(opened["barSnapshot"]["retrievedAt"],
                         "2026-08-27T18:59:33Z")
        self.assertEqual(opened["barSnapshot"]["adjustmentPolicy"],
                         EODHD_ADJUSTMENT_POLICY)
        self.assertEqual(opened["barSnapshot"]["revisionPolicy"],
                         EODHD_REVISION_POLICY)
        self.assertNotIn("bars", opened["barSnapshot"])
        response_text = json.dumps(opened, sort_keys=True)
        self.assertNotIn("api_token", response_text)
        self.assertNotIn('"open": 200', response_text)

        materialized = opened["materialization"]
        version = datasets.ensure_dataset_version(
            self.config,
            materialized["dataset"]["datasetId"],
            materialized["dataset"]["datasetVersionId"],
        )
        self.assertEqual(version["protocolId"], PROTOCOL_ID)
        capability = version["capabilities"]["basicWorkflow"]
        self.assertEqual(capability["protocol"],
                         "trade.app.basic-workflow-dataset/v3")
        self.assertEqual(capability["descriptor"]["protocolId"], PROTOCOL_ID)
        self.assertEqual(capability["descriptor"]["protocolVersion"],
                         V3_PROTOCOL_VERSION)
        self.assertEqual(capability["descriptor"]["profile"], V3_PROFILE_ID)
        manifest_dataset = version["manifest"]["dataset"]
        details = manifest_dataset["source"]["details"]
        for field in (
            "retrievedAt",
            "rawSha256",
            "adjustmentPolicy",
            "revisionPolicy",
        ):
            self.assertEqual(details[field], opened["barSnapshot"][field])
        self.assertNotIn("api_token", json.dumps(manifest_dataset, sort_keys=True))
        self.assertNotIn('"bars": [', json.dumps(manifest_dataset, sort_keys=True))

        stored = datasets.verify_dataset_version_id(
            self.config,
            materialized["dataset"]["datasetVersionId"],
        )
        _version, dataset_authority = verify_dataset_version_storage_authority(
            self.config["releaseRoot"],
            stored,
        )
        dataset = create_dataset_handle(dataset_authority)
        sampler_ref = materialized["sampler"]
        sampler_definition = samplers.get_sampler(
            self.config,
            sampler_ref["samplerId"],
            sampler_ref["version"],
        )
        sampler_authority = verify_sampler_runtime_bundle_authority(
            sampler_definition
        )
        with tempfile.TemporaryDirectory() as execution_root:
            runtime = create_verified_sampler_runtime(
                sampler_authority,
                dataset,
                {"decisionPeriod": "day"},
                execution_root=execution_root,
            )
            try:
                samples = list(runtime)
            finally:
                runtime.close()
        self.assertEqual(len(samples), 20)
        self.assertEqual(samples[0].decision_time, "2026-07-30T20:15:00Z")
        first_bar = samples[0].data["price"]["day"]["US-TSLA"]
        self.assertEqual(first_bar["eventTime"], "2026-07-30T20:00:00Z")
        self.assertEqual(first_bar["volume"], 2_000_000.0)
        self.assertEqual(
            samples[0].provenance["price.day.US-TSLA"]["availableAt"],
            samples[0].decision_time,
        )
        self.assertEqual(len(jobs.submissions), 1)

    def test_v2_then_v3_open_uses_distinct_profile_frozen_pipelines(self):
        legacy = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        legacy_opened = open_instrument(
            self.config,
            {
                "snapshotId": legacy["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="legacy-session",
            providers=self.providers,
        )
        state_path = (
            Path(self.config["controlRoot"])
            / "application-protocols"
            / "basic-workflow"
            / "market"
            / "managed-pipelines.json"
        )
        legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
        legacy_state["pipelines"]["day"].pop("profile")
        state_path.write_text(json.dumps(legacy_state), encoding="utf-8")

        raw, rows = _generated_eodhd_payload()

        def fetch_json(url, *, limit):
            return raw, rows

        provider = EodhdDemoSnapshotProvider(
            fetch_json=fetch_json,
            today=date(2026, 8, 26),
        )
        providers = {provider.provider_id: provider}
        with mock.patch.object(
            market_data_module.engine_clock,
            "utc_now",
            return_value="2026-08-27T18:59:33Z",
        ):
            current = sync_market(
                self.config,
                {"providerId": EODHD_DEMO_PROVIDER_ID},
                providers=providers,
            )
            current_opened = open_instrument(
                self.config,
                {
                    "snapshotId": current["snapshot"]["snapshotId"],
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
                prepared_store=store,
                job_manager=jobs,
                session_identity="ohlcv-session",
                providers=providers,
            )

        self.assertNotEqual(
            legacy_opened["materialization"]["pipeline"]["pipelineId"],
            current_opened["materialization"]["pipeline"]["pipelineId"],
        )
        current_pipeline = market_service_module.pipeline_service.load_pipeline_version_details(
            self.config,
            current_opened["materialization"]["pipeline"]["pipelineId"],
            current_opened["materialization"]["pipeline"]["version"],
        )["definition"]
        self.assertEqual(
            current_pipeline["instances"]["universe"]["moduleId"],
            "basic-ohlcv-price-map-universe",
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {record["profile"] for record in state["pipelines"].values()},
            {PROFILE_ID, V3_PROFILE_ID},
        )
        self.assertEqual(
            set(state["pipelines"]),
            {"day", "day-ohlcv-v3-g3"},
        )
        self.assertEqual(
            state["pipelines"]["day-ohlcv-v3-g3"]["generation"],
            3,
        )
        self.assertEqual(len(jobs.submissions), 2)

    def test_server_projection_cache_persists_exact_result_slice(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            {
                "snapshotId": synced["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="projection-session",
            providers=self.providers,
        )
        request = {
            "backtestId": opened["job"]["backtestId"],
            "paths": ["cycles.time", "cycles.data.price"],
            "temporaryModules": [],
        }
        result_digest = "sha256:" + ("c" * 64)

        engine_calls = 0

        def write_slice(_config, backtest_id, paths, modules, destination, **_kwargs):
            nonlocal engine_calls
            engine_calls += 1
            self.assertEqual(backtest_id, opened["job"]["backtestId"])
            self.assertEqual(paths, sorted(request["paths"]))
            self.assertEqual(modules, [])
            destination.write_text(
                json.dumps({"cycles": {"time": ["2026-01-02T21:00:00Z"]}}),
                encoding="utf-8",
            )
            return {
                "path": destination,
                "cache": {
                    "hit": engine_calls > 1,
                    "cacheKey": "sha256:" + ("d" * 64),
                    "payloadDigest": "sha256:" + ("e" * 64),
                    "payloadSize": destination.stat().st_size,
                },
            }

        with (
            mock.patch.object(
                market_service_module.result_repository,
                "get_backtest_result_view",
                return_value={
                    "status": "completed",
                    "resultContentDigest": result_digest,
                },
            ),
            mock.patch.object(
                market_service_module.result_projection_service,
                "write_backtest_result_slice_cached",
                side_effect=write_slice,
            ) as writer,
        ):
            first = project_result_cached(
                self.config,
                request,
                session_identity="projection-session",
            )
            second = project_result_cached(
                self.config,
                request,
                session_identity="projection-session",
            )
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(first["result"], second["result"])
        self.assertEqual(writer.call_count, 2)
        self.assertFalse(
            (
                Path(self.config["controlRoot"])
                / "application-protocols"
                / "basic-workflow"
                / "market"
                / "projection-cache"
            ).exists()
        )

    def test_watchlist_snapshot_jobs_publish_bars_without_a_backtest(self):
        with mock.patch.object(
            self.provider,
            "download_bars",
            wraps=self.provider.download_bars,
        ) as download:
            synced = sync_market(
                self.config,
                {"providerId": DEFAULT_PROVIDER_ID},
                providers=self.providers,
            )
            self.assertEqual(len(synced["snapshotJobs"]), 1)
            self.assertEqual(
                run_snapshot_jobs(
                    self.config,
                    providers=self.providers,
                ),
                1,
            )
            first_state = market_state(self.config)
            self.assertEqual(first_state["snapshotJobs"][-1]["status"], "completed")
            self.assertEqual(len(first_state["barSnapshots"]), 1)

            set_watchlist(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentIds": [],
                },
            )
            watched = set_watchlist(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentIds": ["US-AAPL"],
                },
            )
            self.assertEqual(len(watched["snapshotJobs"]), 1)
            self.assertEqual(
                run_snapshot_jobs(
                    self.config,
                    providers=self.providers,
                ),
                1,
            )
            store = PreparedBacktestSubmissionStore()
            opened = open_instrument(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
                prepared_store=store,
                job_manager=_PreparedBoundaryJobManager(store),
                session_identity="saved-snapshot-session",
                providers=self.providers,
            )
            self.assertTrue(opened["accepted"])
        state = market_state(self.config)
        self.assertEqual(
            [job["status"] for job in state["snapshotJobs"]],
            ["completed", "completed"],
        )
        self.assertEqual(download.call_count, 1)

    def test_chart_cache_materializes_sampler_projection_without_backtest(self):
        with mock.patch.object(
            self.provider,
            "download_bars",
            wraps=self.provider.download_bars,
        ) as download:
            synced = sync_market(
                self.config,
                {"providerId": DEFAULT_PROVIDER_ID},
                providers=self.providers,
            )
            self.assertEqual(run_snapshot_jobs(
                self.config,
                providers=self.providers,
            ), 1)
            selection = {
                "snapshotId": synced["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            }
            opened = open_chart(
                self.config,
                selection,
                providers=self.providers,
            )
            with mock.patch.object(
                market_service_module,
                "_saved_bar_materialization",
                side_effect=AssertionError("warm chart reopened its Dataset"),
            ):
                reopened = open_chart(
                    self.config,
                    selection,
                    providers=self.providers,
                )
        self.assertTrue(opened["ready"])
        self.assertEqual(opened, reopened)
        self.assertEqual(download.call_count, 1)
        catalog = market_service_module.chart_module_catalog(self.config)
        with mock.patch.object(
            market_service_module.control_state,
            "load_state",
            side_effect=AssertionError("warm chart catalog reloaded the Module index"),
        ):
            self.assertEqual(
                market_service_module.chart_module_catalog(self.config),
                catalog,
            )
        self.assertEqual(len(catalog["modules"]), 22)
        sample = opened["materialization"]["sampleResult"]
        self.assertEqual(sample["cycleCount"], 3)
        self.assertEqual(
            backtest_results.list_backtests(self.config, include_archived=True),
            [],
        )
        projection = project_sample_result_cached(self.config, {
            "sampleResultId": sample["sampleResultId"],
            "paths": ["cycles.data.price.day.US-AAPL"],
            "temporaryModules": [],
        })
        self.assertTrue(projection["cache"]["hit"])
        self.assertEqual(len(projection["result"]["cycles"]), 3)
        visualizations = sample_visualization_service.list_sample_visualizations(
            self.config,
            sample["sampleResultId"],
        )
        self.assertEqual(len(visualizations), 1)
        self.assertEqual(visualizations[0]["revision"], 1)
        definitions = list(
            module_definitions.load_pipeline_definitions(self.config).values()
        )

        def latest_version(module_id):
            return max(
                (
                    value for value in definitions
                    if value["kind"] == "Signal" and value["moduleId"] == module_id
                ),
                key=lambda value: int(value["version"]),
            )["version"]

        temporary_modules = [
            {
                "instanceId": "chart-source",
                "kind": "Signal",
                "moduleId": "basic-price-close-selector",
                "version": latest_version("basic-price-close-selector"),
                "config": {"decisionPeriod": "day", "instrumentId": "US-AAPL"},
                "inputs": {"price": "price"},
                "outputs": {"close": "chart.close"},
            },
            {
                "instanceId": "chart-sma",
                "kind": "Signal",
                "moduleId": "sma-indicator",
                "version": latest_version("sma-indicator"),
                "config": {"period": 2},
                "inputs": {"value": "chart.close"},
                "outputs": {"sma": "chart.sma"},
            },
        ]
        signal_projection = project_sample_result_cached(self.config, {
            "sampleResultId": sample["sampleResultId"],
            "paths": ["cycles.data.chart.sma"],
            "temporaryModules": temporary_modules,
        })
        self.assertEqual(
            [cycle["data"]["chart"]["sma"] for cycle in signal_projection["result"]["cycles"]],
            [None, 102.0, 102.5],
        )

    def test_interactive_open_promotes_queued_chart_cache(self):
        provider = _MultiFixtureProvider()
        providers = {provider.provider_id: provider}
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=providers,
        )
        snapshot = synced["snapshot"]
        instrument = next(
            item for item in snapshot["instruments"]
            if item["instrumentId"] == "US-NVDA"
        )
        promoted = market_service_module._promote_snapshot_job(
            self.config,
            snapshot,
            instrument,
            "day",
        )
        self.assertEqual(promoted["status"], "queued")
        claimed = market_service_module._claim_snapshot_job(self.config)
        self.assertEqual(claimed["instrumentId"], "US-NVDA")

    def test_interactive_open_requeues_failed_chart_cache(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        first = market_service_module._claim_snapshot_job(self.config)
        market_service_module._finish_snapshot_job(
            self.config,
            first["jobId"],
            error="RuntimeError: snapshot acquisition failed",
        )
        snapshot = synced["snapshot"]
        instrument = next(
            item for item in snapshot["instruments"]
            if item["instrumentId"] == first["instrumentId"]
        )
        retried = market_service_module._promote_snapshot_job(
            self.config,
            snapshot,
            instrument,
            first["period"],
        )
        self.assertNotEqual(retried["jobId"], first["jobId"])
        self.assertEqual(retried["status"], "queued")
        claimed = market_service_module._claim_snapshot_job(self.config)
        self.assertEqual(claimed["jobId"], retried["jobId"])

    def test_materialization_cache_is_owner_scoped_and_content_addressed(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        request = {
            "snapshotId": synced["snapshot"]["snapshotId"],
            "instrumentId": "US-AAPL",
            "period": "day",
        }
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        first = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="session-token-a",
            owner_identity="user-a",
            providers=self.providers,
        )
        first_job_id = first["job"]["jobId"]
        self.assertFalse(first["cache"]["materializationHit"])
        jobs.jobs[first_job_id]["status"] = "completed"
        jobs.jobs[first_job_id]["phase"] = "completed"
        with mock.patch.object(
            market_service_module.module_definitions,
            "load_pipeline_definitions",
            side_effect=AssertionError("cache hit rebuilt the Module catalog"),
        ):
            cached = open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="session-token-b",
                owner_identity="user-a",
                providers=self.providers,
            )
        self.assertEqual(cached["job"]["status"], "completed")
        self.assertTrue(cached["cache"]["materializationHit"])
        self.assertEqual(cached["job"]["backtestId"], first["job"]["backtestId"])
        self.assertEqual(
            cached["materialization"]["visualizationSaveRequest"],
            first["materialization"]["visualizationSaveRequest"],
        )
        self.assertEqual(len(jobs.submissions), 1)

        other_owner = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="session-token-c",
            owner_identity="user-b",
            providers=self.providers,
        )
        self.assertNotEqual(
            other_owner["job"]["backtestId"],
            first["job"]["backtestId"],
        )
        self.assertEqual(len(jobs.submissions), 2)

        original_download = self.provider.download_bars

        def changed_download(instrument, period):
            value = original_download(instrument, period)
            value["bars"][-1]["close"] += 0.25
            return value

        original_sync = self.provider.sync_catalog

        def changed_catalog():
            value = original_sync()
            value["asOf"] = "2026-08-27T12:00:00Z"
            return value

        with (
            mock.patch.object(
                self.provider,
                "download_bars",
                side_effect=changed_download,
            ),
            mock.patch.object(
                self.provider,
                "sync_catalog",
                side_effect=changed_catalog,
            ),
        ):
            refreshed = sync_market(
                self.config,
                {"providerId": DEFAULT_PROVIDER_ID},
                providers=self.providers,
            )
            changed_request = {
                **request,
                "snapshotId": refreshed["snapshot"]["snapshotId"],
            }
            changed = open_instrument(
                self.config,
                changed_request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="session-token-b",
                owner_identity="user-a",
                providers=self.providers,
            )
            self.assertNotEqual(
                changed["materialization"]["dataset"]["datasetVersionId"],
                first["materialization"]["dataset"]["datasetVersionId"],
            )
            self.assertNotEqual(
                changed["job"]["backtestId"],
                first["job"]["backtestId"],
            )
            self.assertEqual(len(jobs.submissions), 3)
            jobs.jobs[changed["job"]["jobId"]]["status"] = "failed"
            jobs.jobs[changed["job"]["jobId"]]["phase"] = "failed"
            jobs.jobs[changed["job"]["jobId"]]["error"] = "deterministic failure"
            failed = open_instrument(
                self.config,
                changed_request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="session-token-b",
                owner_identity="user-a",
                providers=self.providers,
            )
        self.assertEqual(failed["job"]["status"], "queued")
        self.assertIsNone(failed["job"]["error"])
        self.assertFalse(failed["cache"]["materializationHit"])
        self.assertNotEqual(failed["job"]["backtestId"], changed["job"]["backtestId"])
        self.assertEqual(len(jobs.submissions), 4)

    def test_four_instruments_reopen_from_same_owner_without_cross_wiring(self):
        instruments = [
            ("US-AAPL", "AAPL", "Apple Inc."),
            ("US-AMZN", "AMZN", "Amazon.com, Inc."),
            ("US-META", "META", "Meta Platforms, Inc."),
            ("US-MSFT", "MSFT", "Microsoft Corporation"),
        ]

        class MultiInstrumentProvider:
            provider_id = DEFAULT_PROVIDER_ID

            def __init__(self):
                self.downloads = []

            def sync_catalog(self):
                return {
                    "asOf": "2026-08-26T12:00:00Z",
                    "instruments": [
                        {
                            "instrumentId": instrument_id,
                            "symbol": symbol,
                            "name": name,
                            "exchange": "NASDAQ",
                            "currency": "USD",
                            "assetType": "stock",
                            "availablePeriods": ["day"],
                        }
                        for instrument_id, symbol, name in instruments
                    ],
                }

            def download_bars(self, instrument, period):
                if period != "day":
                    raise AssertionError("The service changed the requested period.")
                instrument_id = instrument["instrumentId"]
                offset = next(
                    index for index, value in enumerate(instruments)
                    if value[0] == instrument_id
                )
                self.downloads.append((instrument_id, period))
                base = 100.0 + offset * 10.0
                return {
                    "providerId": self.provider_id,
                    "asOf": "2026-08-26T12:01:00Z",
                    "period": "day",
                    "bars": [
                        {
                            "time": "2026-01-02T21:00:00Z",
                            "open": base,
                            "close": base + 1.0,
                            "high": base + 2.0,
                            "low": base - 1.0,
                        },
                        {
                            "time": "2026-01-05T21:00:00Z",
                            "open": base + 1.0,
                            "close": base + 3.0,
                            "high": base + 4.0,
                            "low": base,
                        },
                    ],
                }

        provider = MultiInstrumentProvider()
        providers = {provider.provider_id: provider}
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=providers,
        )
        snapshot_id = synced["snapshot"]["snapshotId"]
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        cold = {}
        for instrument_id, _symbol, _name in instruments:
            opened = open_instrument(
                self.config,
                {
                    "snapshotId": snapshot_id,
                    "instrumentId": instrument_id,
                    "period": "day",
                },
                prepared_store=store,
                job_manager=jobs,
                session_identity="cold-browser-session",
                owner_identity="stable-user",
                providers=providers,
            )
            self.assertFalse(opened["cache"]["materializationHit"])
            jobs.jobs[opened["job"]["jobId"]]["status"] = "completed"
            jobs.jobs[opened["job"]["jobId"]]["phase"] = "completed"
            cold[instrument_id] = opened

        hot = {}
        with mock.patch.object(
            market_service_module,
            "_validate_cached_open_response",
            side_effect=AssertionError("same-process cache hit repeated deep validation"),
        ):
            for instrument_id, _symbol, _name in instruments:
                reopened = open_instrument(
                    self.config,
                    {
                        "snapshotId": snapshot_id,
                        "instrumentId": instrument_id,
                        "period": "day",
                    },
                    prepared_store=store,
                    job_manager=jobs,
                    session_identity="second-browser-session",
                    owner_identity="stable-user",
                    providers=providers,
                )
                self.assertTrue(reopened["cache"]["materializationHit"])
                self.assertEqual(
                    reopened["instrument"]["instrumentId"], instrument_id
                )
                hot[instrument_id] = reopened

        self.assertEqual(len(jobs.submissions), 4)
        self.assertEqual(
            provider.downloads,
            [(instrument_id, "day") for instrument_id, _symbol, _name in instruments],
        )
        self.assertEqual(
            len({value["job"]["backtestId"] for value in cold.values()}),
            4,
        )
        self.assertEqual(
            len({value["materialization"]["dataset"]["datasetVersionId"] for value in cold.values()}),
            4,
        )
        self.assertEqual(
            len({
                value["materialization"]["visualizationSaveRequest"]["visualizationId"]
                for value in cold.values()
            }),
            4,
        )
        for instrument_id, cold_opened in cold.items():
            hot_opened = hot[instrument_id]
            self.assertEqual(hot_opened["job"], jobs.jobs[cold_opened["job"]["jobId"]])
            self.assertEqual(
                hot_opened["materialization"],
                cold_opened["materialization"],
            )

    def test_changed_cache_response_is_deeply_revalidated(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        request = {
            "snapshotId": synced["snapshot"]["snapshotId"],
            "instrumentId": "US-AAPL",
            "period": "day",
        }
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="first-session",
            owner_identity="stable-owner",
            providers=self.providers,
        )
        jobs.jobs[opened["job"]["jobId"]]["status"] = "completed"
        jobs.jobs[opened["job"]["jobId"]]["phase"] = "completed"
        state_path = (
            Path(self.config["controlRoot"])
            / "application-protocols"
            / "basic-workflow"
            / "market"
            / "materializations.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["materializations"]), 1)
        state["materializations"][0]["response"]["materialization"]["pipeline"][
            "contentDigest"
        ] = "sha256:" + ("0" * 64)
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        with self.assertRaises(ValueError):
            open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="second-session",
                owner_identity="stable-owner",
                providers=self.providers,
            )
        self.assertEqual(len(jobs.submissions), 1)

    def test_v3_generation_one_reference_rotates_before_new_submission(self):
        raw, rows = _generated_eodhd_payload()
        provider = EodhdDemoSnapshotProvider(
            fetch_json=lambda _url, *, limit: (raw, rows),
            today=date(2026, 8, 26),
        )
        providers = {provider.provider_id: provider}
        with mock.patch.object(
            market_data_module.engine_clock,
            "utc_now",
            return_value="2026-08-27T18:59:33Z",
        ):
            synced = sync_market(
                self.config,
                {"providerId": EODHD_DEMO_PROVIDER_ID},
                providers=providers,
            )
            request = {
                "snapshotId": synced["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            }
            store = PreparedBacktestSubmissionStore()
            jobs = _PreparedBoundaryJobManager(store)
            first = open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="generation-session",
                providers=providers,
            )
            state_path = (
                Path(self.config["controlRoot"])
                / "application-protocols"
                / "basic-workflow"
                / "market"
                / "managed-pipelines.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            stale = state["pipelines"].pop("day-ohlcv-v3-g3")
            stale["generation"] = 1
            state["schemaVersion"] = 1
            state["pipelines"]["day-ohlcv-v3"] = stale
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            second = open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="generation-session",
                providers=providers,
            )
        self.assertNotEqual(
            first["materialization"]["pipeline"]["pipelineId"],
            second["materialization"]["pipeline"]["pipelineId"],
        )
        rotated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(rotated["pipelines"]),
            {"day-ohlcv-v3", "day-ohlcv-v3-g3"},
        )
        self.assertEqual(len(jobs.submissions), 2)

    def test_cached_managed_pipeline_keeps_its_frozen_module_versions(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        request = {
            "snapshotId": synced["snapshot"]["snapshotId"],
            "instrumentId": "US-AAPL",
            "period": "day",
        }
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        first = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        installed = market_service_module.module_definitions.load_pipeline_definitions(
            self.config
        )
        augmented = dict(installed)
        required_ids = {
            value[1]
            for value in market_service_module.MODULE_REQUIREMENTS.values()
        }
        for key, definition in installed.items():
            if definition.get("moduleId") not in required_ids:
                continue
            newer = copy.deepcopy(definition)
            newer["version"] = str(int(definition["version"]) + 1)
            newer["contentDigest"] = "sha256:" + (
                f"{len(augmented) + 1:064x}"[-64:]
            )
            augmented[f"{key}-newer"] = newer
        with mock.patch.object(
            market_service_module.module_definitions,
            "load_pipeline_definitions",
            return_value=augmented,
        ):
            cached = open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="test-session",
                providers=self.providers,
            )
        self.assertEqual(
            cached["materialization"]["pipeline"],
            first["materialization"]["pipeline"],
        )
        self.assertEqual(len(jobs.submissions), 1)

    def test_bar_snapshot_projection_survives_catalog_resync_for_retained_instrument(self):
        first = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            {
                "snapshotId": first["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )

        original_sync_catalog = self.provider.sync_catalog

        def resynced_catalog():
            catalog = original_sync_catalog()
            catalog["asOf"] = "2026-08-27T12:00:00Z"
            return catalog

        with mock.patch.object(
            self.provider,
            "sync_catalog",
            side_effect=resynced_catalog,
        ):
            second = sync_market(
                self.config,
                {"providerId": DEFAULT_PROVIDER_ID},
                providers=self.providers,
            )
        self.assertNotEqual(
            first["snapshot"]["snapshotId"],
            second["snapshot"]["snapshotId"],
        )
        projected = market_state(self.config)["barSnapshots"]
        self.assertEqual(len(projected), 1)
        self.assertEqual(
            projected[0]["catalogSnapshotId"],
            first["snapshot"]["snapshotId"],
        )
        self.assertEqual(
            projected[0]["datasetVersionId"],
            opened["materialization"]["dataset"]["datasetVersionId"],
        )

    def test_failed_dataset_publication_does_not_create_bar_snapshot_projection(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        with mock.patch.object(
            market_service_module.basic_dataset,
            "register_dataset",
            side_effect=ValueError("publication failed"),
        ), self.assertRaisesRegex(ValueError, "publication failed"):
            open_instrument(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
                prepared_store=PreparedBacktestSubmissionStore(),
                job_manager=_PreparedBoundaryJobManager(
                    PreparedBacktestSubmissionStore()
                ),
                session_identity="test-session",
                providers=self.providers,
            )
        self.assertEqual(market_state(self.config)["barSnapshots"], [])

    def test_projection_is_committed_after_dataset_even_if_later_composition_fails(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        with mock.patch.object(
            market_service_module,
            "_managed_pipeline",
            side_effect=ValueError("composition failed"),
        ), self.assertRaisesRegex(ValueError, "composition failed"):
            open_instrument(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
                prepared_store=PreparedBacktestSubmissionStore(),
                job_manager=_PreparedBoundaryJobManager(
                    PreparedBacktestSubmissionStore()
                ),
                session_identity="test-session",
                providers=self.providers,
            )
        projection = market_state(self.config)["barSnapshots"]
        self.assertEqual(len(projection), 1)
        self.assertEqual(projection[0]["lastTime"], "2026-01-06T21:00:00Z")

    def test_corrupt_bar_snapshot_sidecar_fails_closed(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        store = PreparedBacktestSubmissionStore()
        open_instrument(
            self.config,
            {
                "snapshotId": synced["snapshot"]["snapshotId"],
                "instrumentId": "US-AAPL",
                "period": "day",
            },
            prepared_store=store,
            job_manager=_PreparedBoundaryJobManager(store),
            session_identity="test-session",
            providers=self.providers,
        )
        state_path = (
            Path(self.config["controlRoot"])
            / "application-protocols"
            / "basic-workflow"
            / "market"
            / "bar-snapshots.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["barSnapshots"][0]["lastTime"] = "2026-01-05T21:00:00Z"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "canonical Dataset Version"):
            market_state(self.config)

    def test_watchlist_rejects_unknown_instrument_without_fallback(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        with self.assertRaisesRegex(ValueError, "unknown instrumentId"):
            set_watchlist(
                self.config,
                {
                    "snapshotId": synced["snapshot"]["snapshotId"],
                    "instrumentIds": ["US-NOT-THERE"],
                },
            )

    def test_managed_pipeline_reference_tampering_fails_before_submission(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        request = {
            "snapshotId": synced["snapshot"]["snapshotId"],
            "instrumentId": "US-AAPL",
            "period": "day",
        }
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        pipeline_id = opened["materialization"]["pipeline"]["pipelineId"]
        known_pipelines = pipelines.load_pipelines(self.config)
        state_path = (
            Path(self.config["controlRoot"])
            / "application-protocols"
            / "basic-workflow"
            / "market"
            / "managed-pipelines.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["pipelines"]["day"]["contentDigest"] = "sha256:" + ("0" * 64)
        state_path.write_text(
            json.dumps(state, sort_keys=True),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError,
            "exact reference does not match",
        ):
            open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="test-session",
                providers=self.providers,
            )
        self.assertEqual(len(jobs.submissions), 1)
        self.assertEqual(pipelines.load_pipelines(self.config), known_pipelines)
        self.assertEqual(len(pipelines.pipeline_versions(self.config, pipeline_id)), 2)

    def test_managed_pipeline_scaffold_drift_requires_explicit_rotation(self):
        synced = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        request = {
            "snapshotId": synced["snapshot"]["snapshotId"],
            "instrumentId": "US-AAPL",
            "period": "day",
        }
        store = PreparedBacktestSubmissionStore()
        jobs = _PreparedBoundaryJobManager(store)
        opened = open_instrument(
            self.config,
            request,
            prepared_store=store,
            job_manager=jobs,
            session_identity="test-session",
            providers=self.providers,
        )
        pipeline_id = opened["materialization"]["pipeline"]["pipelineId"]
        original_scaffold = market_service_module.build_pipeline_scaffold

        def changed_scaffold(*args, **kwargs):
            value = original_scaffold(*args, **kwargs)
            value["config"]["observationInput"]["whitelist"].append(
                "unexpected.data"
            )
            return value

        with mock.patch.object(
            market_service_module,
            "build_pipeline_scaffold",
            side_effect=changed_scaffold,
        ), self.assertRaisesRegex(
            ValueError,
            "scaffold has changed",
        ):
            open_instrument(
                self.config,
                request,
                prepared_store=store,
                job_manager=jobs,
                session_identity="new-session",
                owner_identity="new-owner",
                providers=self.providers,
            )
        self.assertEqual(len(jobs.submissions), 1)
        self.assertEqual(len(pipelines.pipeline_versions(self.config, pipeline_id)), 2)

    def test_user_cleared_watchlist_is_not_replaced_by_defaults_on_resync(self):
        first = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        self.assertEqual(
            [item["symbol"] for item in first["watchlist"]],
            ["AAPL"],
        )
        cleared = set_watchlist(
            self.config,
            {
                "snapshotId": first["snapshot"]["snapshotId"],
                "instrumentIds": [],
            },
        )
        self.assertEqual(cleared["watchlist"], [])
        second = sync_market(
            self.config,
            {"providerId": DEFAULT_PROVIDER_ID},
            providers=self.providers,
        )
        self.assertEqual(second["watchlist"], [])

    def test_bar_snapshot_rechecks_finite_positive_and_ohlc_bounds(self):
        instrument = self.provider.sync_catalog()["instruments"][0]
        base = {
            "providerId": DEFAULT_PROVIDER_ID,
            "asOf": "2026-08-26T12:01:00Z",
            "period": "day",
            "bars": [
                {
                    "time": "2026-01-02T21:00:00Z",
                    "open": 100.0,
                    "close": 101.0,
                    "high": 102.0,
                    "low": 99.0,
                }
            ],
        }
        invalid = (
            ({**base["bars"][0], "open": float("nan")}, "OHLC values"),
            ({**base["bars"][0], "close": 0.0}, "OHLC values"),
            ({**base["bars"][0], "low": 100.5}, "OHLC lower bound"),
            ({**base["bars"][0], "high": 100.5}, "OHLC upper bound"),
        )
        for bar, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError,
                message,
            ):
                _bar_snapshot(
                    DEFAULT_PROVIDER_ID,
                    instrument,
                    {**base, "bars": [bar]},
                )


if __name__ == "__main__":
    unittest.main()
