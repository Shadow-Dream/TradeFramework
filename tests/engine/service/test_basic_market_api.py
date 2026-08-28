"""Authenticated HTTP boundary for the Basic market application service."""

from __future__ import annotations

import http.client
import json
import secrets
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import engine_service
from application_subsystems import basic as basic_subsystem
from application_protocols.basic_workflow.market_data import DEFAULT_PROVIDER_ID
from builtin_implementations import resources as builtin_resources
from engine.control import auth as trade_auth
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.service import visualizations as visualization_service


def _available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


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
            raise AssertionError("HTTP route changed the exact open request.")
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
            ],
        }


class _PreparedJobManager:
    def __init__(self, store):
        self.store = store

    def submit(self, request, *, prepared_submission_token, session_identity):
        frozen = self.store.consume(
            prepared_submission_token,
            request,
            session_identity=session_identity,
        )
        return {
            "jobId": "job_01M0SCSPWCMW192DPJ4B0PX99R",
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
            "backtestId": "bt_01M0SCSPWCMW192DPJ4B0PX99R",
            "error": None,
            "snapshotHash": frozen["executionSnapshot"]["snapshotHash"],
        }


class BasicMarketHttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
            "allowInsecureAuth": True,
        }
        engine_database.prepare_database(cls.config)
        builtin_resources.install(cls.config)
        trade_auth.ensure_default_user(cls.config)
        password = secrets.token_urlsafe(24)
        with trade_auth.connect(cls.config) as connection:
            now = engine_clock.utc_now()
            connection.execute(
                """
                INSERT INTO users
                (user_id, email, password_hash, role, status, created_at, updated_at)
                VALUES ('basic-market-api-user', 'basic-market-api@example.invalid',
                        ?, 'admin', 'active', ?, ?)
                """,
                (trade_auth.hash_password(password), now, now),
            )
            connection.commit()
        login = trade_auth.login(
            cls.config,
            "basic-market-api@example.invalid",
            password,
            "127.0.0.1",
        )
        cls.cookie = (
            f"{trade_auth.SESSION_COOKIE}={login['token']}; "
            f"{trade_auth.CSRF_COOKIE}={login['csrfToken']}"
        )
        cls.csrf_token = login["csrfToken"]
        cls.session_hash = trade_auth.opaque_token_hash(login["token"])
        engine_service.EngineServiceHandler.config = cls.config
        engine_service.EngineServiceHandler.prepared_backtest_submissions = (
            engine_service.PreparedBacktestSubmissionStore()
        )
        engine_service.EngineServiceHandler.backtest_job_manager = _PreparedJobManager(
            engine_service.EngineServiceHandler.prepared_backtest_submissions
        )
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", _available_port()),
            engine_service.EngineServiceHandler,
        )
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        engine_service.EngineServiceHandler.public_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def request(
        self,
        method,
        path,
        payload=None,
        *,
        authenticated=True,
        csrf=True,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {}
        if authenticated:
            headers["Cookie"] = self.cookie
            if csrf:
                headers["X-CSRF-Token"] = self.csrf_token
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content or b"{}")

    def test_get_market_requires_auth_and_rejects_query_fields(self):
        with mock.patch.object(
            basic_subsystem.market_service,
            "market_state",
            return_value={
                "protocolId": "trade.basic-workflow",
                "snapshot": None,
                "watchlist": [],
                "barSnapshots": [],
                "snapshotJobs": [],
            },
        ) as market_state:
            status, _body = self.request(
                "GET",
                "/api/subsystems/basic/market",
                authenticated=False,
            )
            self.assertEqual(status, 401)
            market_state.assert_not_called()

            status, body = self.request(
                "GET",
                "/api/subsystems/basic/market?fallback=1",
            )
            self.assertEqual(status, 400, body)
            self.assertIn("unsupported query field", body["error"])
            market_state.assert_not_called()

            status, body = self.request("GET", "/api/subsystems/basic/market")
            self.assertEqual(status, 200, body)
            self.assertEqual(body["protocolId"], "trade.basic-workflow")
            market_state.assert_called_once_with(self.config)

    def test_subsystem_catalog_projects_the_registered_basic_page(self):
        status, _body = self.request(
            "GET",
            "/api/subsystems",
            authenticated=False,
        )
        self.assertEqual(status, 401)
        status, body = self.request("GET", "/api/subsystems")
        self.assertEqual(status, 200, body)
        self.assertEqual(
            body,
            {
                "subsystems": [
                    {
                        "subsystemId": "basic",
                        "protocolId": "trade.basic-workflow",
                        "label": "Basic",
                        "pagePath": "/basic-workflow",
                    }
                ]
            },
        )
        status, body = self.request("GET", "/api/subsystems?fallback=1")
        self.assertEqual(status, 400, body)

    def test_all_registered_basic_pages_and_direct_files_require_authentication(self):
        paths = (
            "/basic-workflow",
            (
                "/basic-workflow/workspace"
                "?instrumentId=US-AAPL&snapshotId=snapshot_01"
            ),
            "/basic_workflow.html",
            "/basic_workflow_workspace.html",
        )
        for path in paths:
            with self.subTest(path=path):
                status, _body = self.request(
                    "HEAD",
                    path,
                    authenticated=False,
                )
                self.assertEqual(status, 303)
                status, _body = self.request("HEAD", path)
                self.assertEqual(status, 200)

    def test_all_market_commands_require_csrf_before_dispatch(self):
        calls = (
            ("sync_market", "/api/subsystems/basic/market/sync"),
            ("set_watchlist", "/api/subsystems/basic/watchlist"),
            ("open_instrument", "/api/subsystems/basic/instruments/open"),
            ("project_result_cached", "/api/subsystems/basic/result-projections"),
        )
        for method_name, path in calls:
            with self.subTest(path=path), mock.patch.object(
                basic_subsystem.market_service,
                method_name,
            ) as command:
                status, _body = self.request(
                    "POST",
                    path,
                    {},
                    authenticated=False,
                )
                self.assertEqual(status, 401)
                status, _body = self.request(
                    "POST",
                    path,
                    {},
                    csrf=False,
                )
                self.assertEqual(status, 403)
                command.assert_not_called()

    def test_open_uses_stable_user_owner_and_session_bound_submission(self):
        opened = {
            "snapshotId": "snap_owner",
            "instrument": {"instrumentId": "US-AAPL"},
            "materialization": {
                "dataset": {"datasetVersionId": "1"},
                "pipeline": {"pipelineId": "pipe_owner"},
            },
            "job": {"jobId": "job_owner", "backtestId": "bt_owner"},
        }
        with mock.patch.object(
            basic_subsystem.market_service,
            "open_instrument",
            return_value=opened,
        ) as command:
            status, body = self.request(
                "POST",
                "/api/subsystems/basic/instruments/open",
                {
                    "snapshotId": "snap_owner",
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
            )
        self.assertEqual(status, 202, body)
        self.assertEqual(body, opened)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.kwargs["session_identity"], self.session_hash)
        self.assertEqual(command.call_args.kwargs["owner_identity"], "basic-market-api-user")

    def test_market_commands_reject_query_and_unknown_body_fields(self):
        cases = (
            (
                "/api/subsystems/basic/market/sync",
                {"providerId": DEFAULT_PROVIDER_ID, "fallback": True},
            ),
            (
                "/api/subsystems/basic/watchlist",
                {"snapshotId": "none", "instrumentIds": [], "fallback": True},
            ),
            (
                "/api/subsystems/basic/instruments/open",
                {
                    "snapshotId": "none",
                    "instrumentId": "US-AAPL",
                    "period": "day",
                    "fallback": True,
                },
            ),
            (
                "/api/subsystems/basic/result-projections",
                {
                    "backtestId": "none",
                    "paths": [],
                    "temporaryModules": [],
                    "fallback": True,
                },
            ),
        )
        for path, payload in cases:
            with self.subTest(path=path):
                status, body = self.request("POST", path + "?fallback=1", {})
                self.assertEqual(status, 400, body)
                self.assertIn("unsupported query field", body["error"])
                status, body = self.request("POST", path, payload)
                self.assertEqual(status, 400, body)
                self.assertIn("unsupported field", body["error"])

    def test_valid_http_chain_returns_202_without_raw_bars_or_event_leakage(self):
        provider = _FixtureProvider()
        registry = {provider.provider_id: provider}
        with (
            mock.patch.object(
                basic_subsystem.market_service,
                "default_provider_registry",
                return_value=registry,
            ),
            mock.patch.object(
                engine_service.EngineServiceHandler,
                "append_event",
            ) as append_event,
        ):
            status, synced = self.request(
                "POST",
                "/api/subsystems/basic/market/sync",
                {"providerId": DEFAULT_PROVIDER_ID},
            )
            self.assertEqual(status, 200, synced)
            snapshot_id = synced["snapshot"]["snapshotId"]

            status, watched = self.request(
                "POST",
                "/api/subsystems/basic/watchlist",
                {"snapshotId": snapshot_id, "instrumentIds": ["US-AAPL"]},
            )
            self.assertEqual(status, 200, watched)

            status, opened = self.request(
                "POST",
                "/api/subsystems/basic/instruments/open",
                {
                    "snapshotId": snapshot_id,
                    "instrumentId": "US-AAPL",
                    "period": "day",
                },
            )
            self.assertEqual(status, 202, opened)
            self.assertEqual(opened["job"]["status"], "queued")
            self.assertEqual(
                opened["materialization"]["pipeline"]["protocolId"],
                "trade.basic-workflow",
            )
            self.assertRegex(
                opened["materialization"]["pipeline"]["contentDigest"],
                r"^sha256:[0-9a-f]{64}$",
            )
            self.assertNotEqual(
                opened["materialization"]["pipeline"]["pipelineId"],
                "basic-market-snapshot-day",
            )
            self.assertEqual(
                opened["materialization"]["visualizationSaveRequest"][
                    "expectedRevision"
                ],
                0,
            )
            self.assertEqual(
                opened["materialization"]["visualizationSaveRequest"][
                    "visualizationId"
                ],
                visualization_service.current_visualization_id(
                    opened["job"]["backtestId"]
                ),
            )
            self.assertNotIn('"bars"', json.dumps(opened, sort_keys=True))
            status, market = self.request(
                "GET",
                "/api/subsystems/basic/market",
            )
            self.assertEqual(status, 200, market)
            self.assertEqual(
                market["barSnapshots"],
                [
                    {
                        "catalogSnapshotId": snapshot_id,
                        "providerId": opened["barSnapshot"]["providerId"],
                        "instrumentId": "US-AAPL",
                        "period": "day",
                        "asOf": opened["barSnapshot"]["asOf"],
                        "firstTime": opened["barSnapshot"]["firstTime"],
                        "lastTime": "2026-01-05T21:00:00Z",
                        "barCount": 2,
                        "contentDigest": opened["barSnapshot"]["contentDigest"],
                        "datasetId": opened["materialization"]["dataset"][
                            "datasetId"
                        ],
                        "datasetVersionId": opened["materialization"]["dataset"][
                            "datasetVersionId"
                        ],
                    }
                ],
            )
            self.assertEqual(append_event.call_count, 3)
            event_text = json.dumps(
                [call.args for call in append_event.call_args_list],
                sort_keys=True,
            )
            self.assertNotIn("bars", event_text.lower())
            self.assertNotIn("ohlc", event_text.lower())


if __name__ == "__main__":
    unittest.main()
