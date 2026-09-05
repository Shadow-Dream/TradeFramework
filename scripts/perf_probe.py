#!/usr/bin/env python3
"""Profile Basic chart open and indicator-add latency on production paths."""

from __future__ import annotations

import copy
import cProfile
import functools
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine_service
from application_protocols.basic_workflow import market_service
from application_protocols.basic_workflow.market_data import DEFAULT_PROVIDER_ID
from builtin_implementations import resources as builtin_resources
from builtin_implementations.pipeline.basic_price_close_selector import BasicPriceCloseSelector
from builtin_implementations.pipeline.bollinger_bands_indicator import BollingerBandsIndicator
from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from engine.control import auth as trade_auth
from engine.control import database as engine_database
from engine.control import schema as control_schema
from engine.composition import sample_result_projection as in_process_projection
from engine.core import clock as engine_clock
from engine.jobs.manager import BacktestJobManager, BacktestJobServices
from engine.repository import backtest_results
from engine.repository import module_definitions
from engine.repository import sample_results
from engine.runtime import projection_worker as engine_projection_worker
from engine.service import backtest_results as backtest_result_service
from engine.service import backtests as backtest_service
from engine.service import result_projection as result_projection_service
from engine.service import sample_result_projection as sample_projection_service
from engine.service import sample_visualizations as sample_visualization_service
from engine.service.backtest_submissions import PreparedBacktestSubmissionStore


NODE_PROBE = ROOT / "scripts" / "perf_probe.js"
RESULT_PATH = Path(os.environ.get("TRADE_PERF_RESULT", "/tmp/trade-perf-result.json"))
ARCHIVE = {"status": "archived", "contentDigest": "sha256:" + "0" * 64}


class PerfProvider:
    provider_id = DEFAULT_PROVIDER_ID

    def __init__(self, bar_count=2500):
        self.bar_count = bar_count
        self.download_calls = 0

    def sync_catalog(self):
        return {
            "asOf": "2026-08-28T12:00:00Z",
            "instruments": [{
                "instrumentId": "US-PERF",
                "symbol": "PERF",
                "name": "Deterministic Performance Fixture",
                "exchange": "NASDAQ",
                "currency": "USD",
                "assetType": "stock",
                "availablePeriods": ["day"],
            }],
        }

    def download_bars(self, instrument, period):
        started = time.perf_counter()
        self.download_calls += 1
        if instrument["instrumentId"] != "US-PERF" or period != "day":
            raise AssertionError("Performance probe changed the fixture request.")
        bars = []
        cursor = datetime(2016, 1, 4, 21, tzinfo=timezone.utc)
        index = 0
        while len(bars) < self.bar_count:
            if cursor.weekday() < 5:
                close = 100.0 + index * 0.07 + ((index % 9) - 4) * 0.13
                open_price = close - ((index % 5) - 2) * 0.08
                bars.append({
                    "time": cursor.isoformat().replace("+00:00", "Z"),
                    "open": round(open_price, 4),
                    "close": round(close, 4),
                    "high": round(max(open_price, close) + 0.6, 4),
                    "low": round(min(open_price, close) - 0.6, 4),
                })
                index += 1
            cursor += timedelta(days=1)
        SERVER_TIMINGS.append({
            "name": "provider.download_bars",
            "startEpochMs": time.time() * 1000 - (time.perf_counter() - started) * 1000,
            "durationMs": (time.perf_counter() - started) * 1000,
            "thread": threading.current_thread().name,
        })
        return {
            "providerId": self.provider_id,
            "asOf": "2026-08-28T12:01:00Z",
            "period": "day",
            "bars": bars,
        }


SERVER_TIMINGS = []
TIMING_LOCK = threading.Lock()


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def create_session(config):
    trade_auth.ensure_default_user(config)
    password = secrets.token_urlsafe(24)
    now = engine_clock.utc_now()
    with trade_auth.connect(config) as connection:
        connection.execute(
            """
            INSERT INTO users
            (user_id, email, password_hash, role, status, created_at, updated_at)
            VALUES (?, ?, ?, 'admin', 'active', ?, ?)
            """,
            (
                "basic-perf-probe",
                "basic-perf-probe@example.invalid",
                trade_auth.hash_password(password),
                now,
                now,
            ),
        )
        connection.commit()
    return trade_auth.login(
        config,
        "basic-perf-probe@example.invalid",
        password,
        "127.0.0.1",
    )


def manager_services(prepared_store):
    return BacktestJobServices(
        freeze_request=backtest_service.freeze_backtest_request,
        reconcile_result_staging=backtest_results.reconcile_result_staging,
        recover_result_catalog=backtest_result_service.recover_backtest_result_catalog,
        validate_result_archive=result_projection_service.validate_backtest_result_archive,
        consume_prepared_request=prepared_store.consume,
        validate_frozen_admission=backtest_service.require_frozen_backtest_admission,
    )


def timed(name, function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        started_epoch = time.time() * 1000
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            with TIMING_LOCK:
                SERVER_TIMINGS.append({
                    "name": name,
                    "startEpochMs": started_epoch,
                    "durationMs": (time.perf_counter() - started) * 1000,
                    "thread": threading.current_thread().name,
                })
    return wrapper


def initialize_module(implementation, module_id, config):
    definitions = {item["moduleId"]: item for item in BUILTIN_PIPELINE_MODULES}
    definition = definitions[module_id]
    implementation.initialize({
        "key": f"perf.{module_id}",
        "kind": "Signal",
        "moduleId": module_id,
        "version": "1",
        "archive": ARCHIVE,
        "config": config,
        "inputs": copy.deepcopy(definition["ports"]["inputs"]),
        "outputs": copy.deepcopy(definition["ports"]["outputs"]),
    })
    return implementation


def module_microbenchmark(bar_count=2500, repetitions=100):
    selector = initialize_module(
        BasicPriceCloseSelector(),
        "basic-price-close-selector",
        {"decisionPeriod": "day", "instrumentId": "US-PERF"},
    )
    bands = initialize_module(
        BollingerBandsIndicator(),
        "bollinger-bands-indicator",
        {"period": 20, "k": 2},
    )
    bars = [{
        "open": 100.0 + index * 0.01,
        "close": 100.2 + index * 0.01,
        "high": 100.8 + index * 0.01,
        "low": 99.5 + index * 0.01,
    } for index in range(bar_count)]
    started = time.perf_counter()
    for _ in range(repetitions):
        for bar in bars:
            close = selector.invoke({
                "price": {"day": {"US-PERF": bar}}
            })["close"]
            bands.invoke({"price": close})
    elapsed = time.perf_counter() - started
    selector_metrics = selector.runtime_metrics()
    bands_metrics = bands.runtime_metrics()
    selector.close()
    bands.close()
    scale = 1000 / repetitions
    return {
        "barCount": bar_count,
        "repetitions": repetitions,
        "graphWallMsPerRun": elapsed * scale,
        "selectorMsPerRun": {
            key: value * scale for key, value in selector_metrics.items()
        },
        "bollingerMsPerRun": {
            key: value * scale for key, value in bands_metrics.items()
        },
    }


def main():
    SERVER_TIMINGS.clear()
    with tempfile.TemporaryDirectory(prefix="trade-basic-perf-") as temporary:
        root = Path(temporary)
        config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
            "allowInsecureAuth": True,
        }
        control_schema.prepare(config)
        engine_database.prepare_database(config)
        builtin_resources.install(config)
        session = create_session(config)
        provider = PerfProvider()
        snapshot = market_service.sync_market(
            config,
            {"providerId": provider.provider_id},
            providers={provider.provider_id: provider},
        )["snapshot"]
        market_service.set_watchlist(config, {
            "snapshotId": snapshot["snapshotId"],
            "instrumentIds": ["US-PERF"],
        })
        market_service.run_snapshot_jobs(
            config,
            providers={provider.provider_id: provider},
        )
        prepared_store = PreparedBacktestSubmissionStore()
        manager = BacktestJobManager(
            config,
            manager_services(prepared_store),
            max_workers=1,
        )

        restorations = []
        for owner, attribute, label in [
            (market_service, "open_chart", "market.open_chart"),
            (market_service, "project_sample_result_cached", "market.project_sample_cached"),
            (sample_projection_service, "write_sample_result_slice_cached", "engine.sample_projection_cached"),
            (sample_projection_service, "write_sample_result_projection_in_worker", "engine.projection_worker"),
            (module_definitions, "load_definition_versions", "engine.load_module_versions"),
            (sample_visualization_service, "save_sample_visualization", "engine.save_sample_visualization"),
        ]:
            original = getattr(owner, attribute)
            restorations.append((owner, attribute, original))
            setattr(owner, attribute, timed(label, original))
        original_submit = manager.submit
        original_execute = manager._execute
        manager.submit = timed("engine.job_submit", original_submit)
        manager._execute = timed("engine.backtest_execute", original_execute)

        engine_service.EngineServiceHandler.config = config
        engine_service.EngineServiceHandler.prepared_backtest_submissions = prepared_store
        engine_service.EngineServiceHandler.backtest_job_manager = manager
        engine_service.EngineServiceHandler.agent_public_url = "http://127.0.0.1:1"
        port = available_port()
        origin = f"http://127.0.0.1:{port}"
        engine_service.EngineServiceHandler.public_url = origin
        server = ThreadingHTTPServer(
            ("127.0.0.1", port), engine_service.EngineServiceHandler
        )
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        original_registry = market_service.default_provider_registry
        market_service.default_provider_registry = lambda: {
            provider.provider_id: provider
        }
        thread.start()
        try:
            query = (
                f"snapshotId={snapshot['snapshotId']}"
                "&instrumentId=US-PERF&period=day"
            )
            environment = {
                **os.environ,
                "TRADE_PERF_ORIGIN": origin,
                "TRADE_PERF_WORKSPACE_URL": (
                    f"{origin}/basic-workflow/workspace?{query}"
                ),
                "TRADE_PERF_SESSION_COOKIE": trade_auth.SESSION_COOKIE,
                "TRADE_PERF_SESSION_TOKEN": session["token"],
                "TRADE_PERF_CSRF_COOKIE": trade_auth.CSRF_COOKIE,
                "TRADE_PERF_CSRF_TOKEN": session["csrfToken"],
            }
            completed = subprocess.run(
                ["node", str(NODE_PROBE)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=240,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Browser performance probe failed.\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            marker = "TRADE_PERF_RESULT="
            records = [
                line[len(marker):]
                for line in completed.stdout.splitlines()
                if line.startswith(marker)
            ]
            if len(records) != 1:
                raise RuntimeError("Browser probe emitted no unique result record.")
            result = json.loads(records[0])
            backtests = backtest_results.list_backtests(
                config, include_archived=True
            )
            if backtests:
                raise RuntimeError(
                    "Performance probe unexpectedly created a Backtest."
                )
            result["backtestCount"] = 0
            chart_cache = json.loads(
                (root / "control/application-protocols/basic-workflow/market/chart-cache.json")
                .read_text(encoding="utf-8")
            )
            sample_result_id = chart_cache["records"][0]["sampleResultId"]
            visualization = sample_visualization_service.list_sample_visualizations(
                config,
                sample_result_id,
            )[0]
            spec = visualization["spec"]
            temporary_modules = list(spec.get("temporaryModules") or [])
            for pane in spec["panes"]:
                temporary_modules.extend(pane.get("temporaryModules") or [])
            references = [
                (module["kind"], module["moduleId"], module["version"])
                for module in temporary_modules
            ]
            definitions, _definition_evidence = module_definitions.load_definition_versions(
                config,
                references,
            )
            output_paths = sorted({
                f"cycles.data.{data_key}"
                for module in temporary_modules
                if module["moduleId"] == "bollinger-bands-indicator"
                for data_key in module["outputs"].values()
            })
            evidence = sample_results.load_sample_result_evidence(
                config,
                sample_result_id,
            )
            destination = root / "in-process-projection.json"
            captured_cycles = []
            captured_metadata = []
            projection_started = time.perf_counter()
            in_process_projection.project_sample_result(
                evidence,
                output_paths,
                temporary_modules,
                definitions,
                destination,
                capture_cycle=captured_cycles.append,
                capture_metadata=captured_metadata.append,
            )
            result["inProcessProjection"] = {
                "durationMs": (time.perf_counter() - projection_started) * 1000,
                "outputSize": destination.stat().st_size,
                "paths": output_paths,
            }
            frames_destination = root / "in-process-frames.json"
            profiler = cProfile.Profile()
            frames_started = time.perf_counter()
            profiler.enable()
            in_process_projection.project_sample_result_frames(
                evidence,
                {"cycles": captured_cycles, "metadata": captured_metadata[0]},
                output_paths,
                temporary_modules,
                definitions,
                frames_destination,
            )
            profiler.disable()
            profiler.dump_stats("/tmp/proj-worker.prof")
            result["inProcessFramesProjection"] = {
                "durationMs": (time.perf_counter() - frames_started) * 1000,
                "outputSize": frames_destination.stat().st_size,
            }
            result["serverTimings"] = sorted(
                SERVER_TIMINGS, key=lambda item: item["startEpochMs"]
            )
            result["providerDownloadCalls"] = provider.download_calls
            result["projectionWorker"] = engine_projection_worker.projection_worker_status()
            result["microbenchmark"] = module_microbenchmark()
            RESULT_PATH.write_text(
                json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(json.dumps({
                "accepted": True,
                "resultPath": str(RESULT_PATH),
                "runCount": len(result["runs"]),
                "serverTimingCount": len(result["serverTimings"]),
                "providerDownloadCalls": provider.download_calls,
                "elapsedMs": {
                    run["label"]: run["elapsedMs"] for run in result["runs"]
                },
            }, indent=2, sort_keys=True))
        finally:
            market_service.default_provider_registry = original_registry
            for owner, attribute, original in reversed(restorations):
                setattr(owner, attribute, original)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            manager.shutdown()
            engine_projection_worker.shutdown_projection_worker()


if __name__ == "__main__":
    main()
