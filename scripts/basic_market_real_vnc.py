#!/usr/bin/env python3
"""Run the Basic market browser smoke against real Trade Engine services.

Only the remote market provider is replaced with a deterministic fixture.  The
HTTP handler, resource publication, prepared submission, Backtest worker,
Result projection, and Visualization repository are the production paths.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine_service
from application_protocols.basic_workflow import market_service
from application_protocols.basic_workflow.market_data import DEFAULT_PROVIDER_ID
from builtin_implementations import resources as builtin_resources
from engine.control import auth as trade_auth
from engine.control import database as engine_database
from engine.control import schema as control_schema
from engine.core import clock as engine_clock
from engine.jobs.manager import BacktestJobManager, BacktestJobServices
from engine.repository import backtest_results
from engine.repository import datasets
from engine.repository import pipelines
from engine.repository import visualizations
from engine.service import backtest_results as backtest_result_service
from engine.service import backtests as backtest_service
from engine.service import result_projection as result_projection_service
from engine.service.backtest_submissions import PreparedBacktestSubmissionStore


NODE_SMOKE = ROOT / "scripts" / "basic_market_real_vnc.js"
SCREENSHOT = Path(
    os.environ.get(
        "TRADE_BASIC_REAL_SCREENSHOT",
        "/tmp/trade-basic-market-home-real-vnc.png",
    )
)
RESULT_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_BASIC_REAL_RESULT_SCREENSHOT",
        "/tmp/trade-basic-market-workspace-real-vnc.png",
    )
)
RETURNED_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_BASIC_REAL_RETURNED_SCREENSHOT",
        "/tmp/trade-basic-market-returned-home-real-vnc.png",
    )
)
GENERIC_RESULT_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_IDENTITY_RESULT_SCREENSHOT",
        "/tmp/trade-generic-result-identity-vnc.png",
    )
)
OVERVIEW_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_IDENTITY_OVERVIEW_SCREENSHOT",
        "/tmp/trade-overview-identity-vnc.png",
    )
)
RESOURCE_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_IDENTITY_RESOURCE_SCREENSHOT",
        "/tmp/trade-resource-detail-identity-vnc.png",
    )
)
PIPELINE_RESOURCE_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_IDENTITY_PIPELINE_RESOURCE_SCREENSHOT",
        "/tmp/trade-pipeline-detail-identity-vnc.png",
    )
)
BACKTEST_RESOURCE_SCREENSHOT = Path(
    os.environ.get(
        "TRADE_IDENTITY_BACKTEST_RESOURCE_SCREENSHOT",
        "/tmp/trade-backtest-detail-identity-vnc.png",
    )
)


class FixtureNasdaqProvider:
    """Deterministic upstream boundary; it does not implement Engine behavior."""

    provider_id = DEFAULT_PROVIDER_ID
    catalog_size = 12_000

    def __init__(self):
        self.catalog_calls = 0
        self.bar_calls = []

    def sync_catalog(self):
        self.catalog_calls += 1
        generated = [
            {
                "instrumentId": f"US-SYM{index:05d}",
                "symbol": f"SYM{index:05d}",
                "name": f"Synthetic Listed Company {index:05d}",
                "exchange": "NASDAQ",
                "currency": "USD",
                "assetType": "stock",
                "availablePeriods": ["day"],
            }
            for index in range(self.catalog_size - 2)
        ]
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
                },
                {
                    "instrumentId": "US-MSFT",
                    "symbol": "MSFT",
                    "name": "Microsoft Corporation",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                    "assetType": "stock",
                    "availablePeriods": ["day"],
                },
                *generated,
            ],
        }

    def download_bars(self, instrument, period):
        self.bar_calls.append((instrument["instrumentId"], period))
        if instrument["instrumentId"] != "US-AAPL" or period != "day":
            raise AssertionError("Browser changed the explicit fixture instrument request.")
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
                {
                    "time": "2026-01-07T21:00:00Z",
                    "open": 102.0,
                    "close": 106.0,
                    "high": 107.0,
                    "low": 101.0,
                },
                {
                    "time": "2026-01-08T21:00:00Z",
                    "open": 106.0,
                    "close": 105.0,
                    "high": 108.0,
                    "low": 104.0,
                },
            ],
        }


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
                "basic-market-real-vnc",
                "basic-market-real-vnc@example.invalid",
                trade_auth.hash_password(password),
                now,
                now,
            ),
        )
        connection.commit()
    return trade_auth.login(
        config,
        "basic-market-real-vnc@example.invalid",
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


def require_browser_result(stdout, prefix):
    marker = prefix + "="
    lines = [line for line in stdout.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise AssertionError(
            f"Browser smoke did not emit one {prefix} record:\n{stdout}"
        )
    return json.loads(lines[0].split("=", 1)[1])


def repository_snapshot(config):
    """Read canonical resource catalogs around the home-only browser phase."""

    return {
        "datasets": datasets.list_datasets(config),
        "pipelines": pipelines.load_pipelines(config),
        "backtests": backtest_results.list_backtests(
            config,
            include_archived=True,
        ),
        "visualizations": visualizations.list_visualizations(config),
    }


def repository_counts(snapshot):
    return {name: len(records) for name, records in snapshot.items()}


def run_browser(environment, mode, prefix):
    completed = subprocess.run(
        ["node", str(NODE_SMOKE)],
        cwd=ROOT,
        env={**environment, "TRADE_BASIC_REAL_MODE": mode},
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Real Basic {mode} browser smoke failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return require_browser_result(completed.stdout, prefix)


def validate_engine_state(config, browser):
    opened = browser["opened"]
    materialized = opened["materialization"]
    backtest_id = opened["job"]["backtestId"]
    dataset = datasets.ensure_dataset_version(
        config,
        materialized["dataset"]["datasetId"],
        materialized["dataset"]["datasetVersionId"],
    )
    pipeline = pipelines.load_pipeline_version(
        config,
        materialized["pipeline"]["pipelineId"],
        materialized["pipeline"]["version"],
    )
    view = backtest_results.get_backtest_result_view(config, backtest_id)
    records = visualizations.list_visualizations(config, backtest_id)
    if dataset["protocolId"] != "trade.basic-workflow":
        raise AssertionError("Materialized Dataset is not Basic protocol-owned.")
    if pipeline["protocolId"] != "trade.basic-workflow":
        raise AssertionError("Materialized Pipeline is not Basic protocol-owned.")
    if view["protocolId"] != "trade.basic-workflow" or view["status"] != "completed":
        raise AssertionError("Canonical Backtest Result is not a completed Basic Result.")
    if len(records) != 1:
        raise AssertionError(f"Expected one canonical Visualization, got {len(records)}.")
    record = records[0]
    if record["revision"] != browser["finalRevision"]:
        raise AssertionError("Browser and repository Visualization revisions differ.")
    instances = record["spec"]["panes"][0]["visualizers"]
    candles = [item for item in instances if item["callback"] == "ohlc.candles"]
    drawings = [item for item in instances if item["callback"] == "drawing.trendLine"]
    drawing_diagnostic = browser["audit"]["workspace"].get("drawingDiagnostic") is not None
    expected_drawing_count = 4 if drawing_diagnostic else 1
    if len(candles) != 1 or len(drawings) != expected_drawing_count:
        raise AssertionError(
            "Canonical Visualization has an unexpected Candles/Trend Line count."
        )
    if candles[0]["params"]["dataKey"] != "price.day.US-AAPL":
        raise AssertionError("Candles did not preserve its explicit Basic DataKey.")
    if any(item["params"]["targetVisualizerId"] != candles[0]["id"] for item in drawings):
        raise AssertionError("Trend Line does not explicitly bind the Candles coordinate resource.")

    with tempfile.TemporaryDirectory(prefix="trade-basic-real-result-") as temporary:
        destination = Path(temporary) / "slice.json"
        result_projection_service.write_backtest_result_slice(
            config,
            backtest_id,
            ["cycles.data.price.day.US-AAPL"],
            [],
            destination,
        )
        result = json.loads(destination.read_text(encoding="utf-8"))
    bars = [cycle["data"]["price"]["day"]["US-AAPL"] for cycle in result["cycles"]]
    if len(bars) != 5 or bars[0]["open"] != 100.0 or bars[-1]["close"] != 105.0:
        raise AssertionError("Canonical Result slice does not contain the fixture OHLC values.")
    return {
        "datasetId": dataset["datasetId"],
        "datasetVersionId": dataset["datasetVersionId"],
        "pipelineId": pipeline["pipelineId"],
        "pipelineVersion": pipeline["version"],
        "backtestId": backtest_id,
        "resultContentDigest": view["resultContentDigest"],
        "visualizationId": record["visualizationId"],
        "visualizationRevision": record["revision"],
        "drawingId": drawings[0]["id"],
        "cycleCount": len(bars),
    }


def main():
    if not os.environ.get("DISPLAY"):
        raise SystemExit("DISPLAY is required, for example DISPLAY=:56.")
    with tempfile.TemporaryDirectory(prefix="trade-basic-market-real-") as temporary:
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
        provider = FixtureNasdaqProvider()
        prepared_store = PreparedBacktestSubmissionStore()
        manager = BacktestJobManager(
            config,
            manager_services(prepared_store),
            max_workers=1,
        )
        engine_service.EngineServiceHandler.config = config
        engine_service.EngineServiceHandler.prepared_backtest_submissions = prepared_store
        engine_service.EngineServiceHandler.backtest_job_manager = manager
        engine_service.EngineServiceHandler.agent_public_url = "http://127.0.0.1:1"
        port = available_port()
        origin = f"http://127.0.0.1:{port}"
        engine_service.EngineServiceHandler.public_url = origin
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            engine_service.EngineServiceHandler,
        )
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        original_registry = market_service.default_provider_registry
        market_service.default_provider_registry = lambda: {provider.provider_id: provider}
        thread.start()
        try:
            environment = {
                **os.environ,
                "TRADE_BASIC_REAL_ORIGIN": origin,
                "TRADE_BASIC_REAL_SESSION_COOKIE": trade_auth.SESSION_COOKIE,
                "TRADE_BASIC_REAL_SESSION_TOKEN": session["token"],
                "TRADE_BASIC_REAL_CSRF_COOKIE": trade_auth.CSRF_COOKIE,
                "TRADE_BASIC_REAL_CSRF_TOKEN": session["csrfToken"],
                "TRADE_BASIC_REAL_SCREENSHOT": str(SCREENSHOT),
                "TRADE_BASIC_REAL_RESULT_SCREENSHOT": str(RESULT_SCREENSHOT),
                "TRADE_BASIC_REAL_RETURNED_SCREENSHOT": str(RETURNED_SCREENSHOT),
            }
            before_home = repository_snapshot(config)
            home_browser = run_browser(
                environment,
                "home-only",
                "BASIC_HOME_VNC_RESULT",
            )
            after_home = repository_snapshot(config)
            if after_home != before_home:
                raise AssertionError(
                    "The Basic home page changed canonical Engine resource catalogs.\n"
                    f"before={repository_counts(before_home)}\n"
                    f"after={repository_counts(after_home)}"
                )
            if provider.catalog_calls != 1 or provider.bar_calls:
                raise AssertionError(
                    "Home-only browser must sync one catalog without downloading bars."
                )

            drawing_diagnostic = os.environ.get("TRADE_BASIC_DRAWING_DIAGNOSTIC") == "1"
            browser = run_browser(
                environment,
                "drawing-diagnostic" if drawing_diagnostic else "full",
                "BASIC_REAL_VNC_RESULT",
            )
            expected_catalog_calls = 1 if drawing_diagnostic else 2
            if provider.catalog_calls != expected_catalog_calls or provider.bar_calls != [
                ("US-AAPL", "day"),
            ]:
                raise AssertionError(
                    "The explicit catalog refresh must remain catalog-only, and only the "
                    "clicked workspace may download the exact AAPL day bars."
                )
            canonical = validate_engine_state(config, browser)
            print(json.dumps({
                "accepted": True,
                "provider": {
                    "providerId": provider.provider_id,
                    "catalogCalls": provider.catalog_calls,
                    "barCalls": provider.bar_calls,
                },
                "canonical": canonical,
                "homeOnly": {
                    **home_browser["home"],
                    "repositoryCountsBefore": repository_counts(before_home),
                    "repositoryCountsAfter": repository_counts(after_home),
                    "repositoryCatalogsUnchanged": True,
                    "providerBarCalls": [],
                },
                "browser": browser["audit"],
                "screenshots": [
                    str(SCREENSHOT),
                    str(RESULT_SCREENSHOT),
                    str(RETURNED_SCREENSHOT),
                    str(GENERIC_RESULT_SCREENSHOT),
                    str(OVERVIEW_SCREENSHOT),
                    str(RESOURCE_SCREENSHOT),
                    str(PIPELINE_RESOURCE_SCREENSHOT),
                    str(BACKTEST_RESOURCE_SCREENSHOT),
                ],
            }, indent=2, sort_keys=True))
        finally:
            market_service.default_provider_registry = original_registry
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            manager.shutdown()


if __name__ == "__main__":
    main()
