#!/usr/bin/env python3
"""Authenticated Engine service API integration tests."""

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

from builtin_implementations import resources as builtin_resources
from engine.core import clock as engine_clock
import engine_service
from engine.control import database as engine_database
from engine.service import control_api as control
from engine.control import auth as trade_auth
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class EngineAuthenticationAndPipelineApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
            "allowInsecureAuth": True,
        }
        engine_database.prepare_database(cls.config)
        builtin_resources.install(cls.config)
        engine_service.EngineServiceHandler.config = cls.config
        engine_service.EngineServiceHandler.prepared_backtest_submissions = (
            engine_service.PreparedBacktestSubmissionStore()
        )
        trade_auth.ensure_default_user(cls.config)
        password = secrets.token_urlsafe(24)
        with trade_auth.connect(cls.config) as connection:
            now = engine_clock.utc_now()
            connection.execute(
                """
                INSERT INTO users
                (user_id, email, password_hash, role, status, created_at, updated_at)
                VALUES ('engine-api-user', 'engine-api@example.invalid', ?, 'admin', 'active', ?, ?)
                """,
                (trade_auth.hash_password(password), now, now),
            )
            connection.commit()
        login = trade_auth.login(
            cls.config, "engine-api@example.invalid", password, "127.0.0.1"
        )
        cls.auth_headers = {
            "Cookie": (
                f"{trade_auth.SESSION_COOKIE}={login['token']}; "
                f"{trade_auth.CSRF_COOKIE}={login['csrfToken']}"
            ),
            "X-CSRF-Token": login["csrfToken"],
        }
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", available_port()), engine_service.EngineServiceHandler
        )
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        engine_service.EngineServiceHandler.public_url = f"http://127.0.0.1:{cls.port}"
        engine_service.EngineServiceHandler.agent_public_url = "http://127.0.0.1:30810"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def request(self, method, path, payload=None, *, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = dict(self.auth_headers) if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content or b"{}")

    def create_pipeline(self, name="API Pipeline"):
        status, body = self.request("POST", "/api/pipelines", {"name": name})
        self.assertEqual(status, 200, body)
        return body

    def test_agent_entry_redirects_authenticated_user_and_preserves_safe_return(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request(
            "GET",
            "/agent?returnTo=%2Fpipeline%3FpipelineId%3Dpipe-1",
            headers=self.auth_headers,
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(
            response.getheader("Location"),
            "http://127.0.0.1:30810/?returnTo=%2Fpipeline%3FpipelineId%3Dpipe-1",
        )
        connection.close()

    def test_agent_entry_rejects_external_or_unknown_return_target(self):
        for return_to in ("https%3A%2F%2Fevil.test", "%2Funknown"):
            status, body = self.request("GET", f"/agent?returnTo={return_to}")
            self.assertEqual(status, 400, body)
            self.assertIn("returnTo", body["error"])

    def test_ui_sync_bootstrap_uses_the_configured_agent_origin(self):
        status, body = self.request("GET", "/api/ui-sync/config")
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {
            "protocolVersion": 1,
            "webSocketUrl": "ws://127.0.0.1:30810/ws/ui",
        })
        status, body = self.request(
            "GET", "/api/ui-sync/config", authenticated=False
        )
        self.assertEqual(status, 401, body)

    def test_obsolete_handoff_and_event_stream_routes_are_removed(self):
        for method, path, payload in (
            ("POST", "/api/agent-handoffs", {}),
            ("POST", "/api/agent-handoffs/exchange", {}),
            ("GET", "/api/events", None),
        ):
            status, body = self.request(method, path, payload)
            self.assertEqual(status, 404, (path, body))

    def signal_draft(self, pipeline_id, name="Signal Pipeline"):
        module = next(
            item
            for item in module_definitions.load_pipeline_definitions(
                self.config
            ).values()
            if item["moduleId"] == "sma-indicator"
        )
        return {
            "pipelineId": pipeline_id,
            "name": name,
            "config": {
                "observationInput": {
                    "whitelist": ["price.close"],
                    "blacklist": [],
                }
            },
            "instances": {
                "sma": {
                    "instanceId": "sma",
                    "kind": "Signal",
                    "moduleId": module["moduleId"],
                    "version": module["version"],
                    "config": {"period": 2},
                    "inputs": {"value": "wire.price"},
                    "outputs": {"sma": "wire.sma"},
                },
            },
            "stages": {},
            "signalGraph": {
                "nodes": ["sma"],
                "inputs": {
                    "price-input": {"dataKey": "price.close", "wire": "wire.price"},
                },
                "outputs": {
                    "sma-output": {"dataKey": "signal.sma", "wire": "wire.sma"},
                },
            },
        }

    def test_control_api_requires_authentication(self):
        status, _body = self.request("GET", "/api/pipelines", authenticated=False)
        self.assertEqual(status, 401)

    def test_visualization_get_and_post_use_the_formal_service_with_auth(self):
        spec = {
            "schemaVersion": 3,
            "datasetId": "prices",
            "timeZone": "UTC",
            "panes": [],
        }
        request = {
            "backtestId": "bt_01K00000000000000000000000",
            "visualizationId": "current",
            "name": "Current",
            "spec": spec,
        }
        record = {
            "visualizationId": "current",
            "backtestId": request["backtestId"],
            "name": "Current",
            "createdAt": "2026-08-11T12:00:00Z",
            "spec": spec,
        }
        with mock.patch.object(
            engine_service.visualization_service,
            "list_visualizations",
            return_value=[record],
        ) as list_visualizations:
            status, _body = self.request(
                "GET",
                "/api/visualizations",
                authenticated=False,
            )
            self.assertEqual(status, 401)
            list_visualizations.assert_not_called()

            status, body = self.request(
                "GET",
                f"/api/visualizations?backtestId={request['backtestId']}",
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body, {"visualizations": [record]})
            list_visualizations.assert_called_once_with(
                self.config,
                request["backtestId"],
            )

        result = {"accepted": True, "visualization": record}
        with (
            mock.patch.object(
                engine_service.visualization_service,
                "save_visualization",
                return_value=result,
            ) as save_visualization,
            mock.patch.object(
                engine_service.EngineServiceHandler,
                "append_event",
            ) as append_event,
        ):
            status, _body = self.request(
                "POST",
                "/api/visualizations",
                request,
                authenticated=False,
            )
            self.assertEqual(status, 401)
            save_visualization.assert_not_called()

            status, body = self.request(
                "POST",
                "/api/visualizations",
                request,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body, result)
            save_visualization.assert_called_once_with(
                self.config,
                request,
                engine_service.visualizer_definition_map(),
            )
            append_event.assert_called_once_with(
                "visualization.saved",
                record,
            )

    def test_control_api_rejects_missing_actions_unknown_fields_and_query_fields(self):
        status, body = self.request(
            "POST", "/api/repository-folders", {"repository": "pipelines", "name": "x"}
        )
        self.assertEqual(status, 400)
        self.assertIn("requires action", body["error"])

        status, body = self.request(
            "POST",
            "/api/account/password",
            {"currentPassword": "x", "newPassword": "y", "fallback": True},
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported field", body["error"])

        status, body = self.request(
            "POST", "/api/data/datasets/download", {"datasetIds": [], "typo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported field", body["error"])

        status, body = self.request(
            "POST", "/api/backtest-compositions/validate?typo=1", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported query field", body["error"])

        status, body = self.request(
            "POST", "/api/backtest-submissions/prepare?typo=1", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported query field", body["error"])

    def test_backtest_validation_is_pure_and_preparation_is_the_command(self):
        validation = {
            "valid": True,
            "pipelineTopology": [],
            "environmentTopology": [],
            "analysisTopology": [],
            "resultContracts": {},
        }
        prepared = {
            **validation,
            "preparedSubmissionToken": "opaque",
            "requestDigest": "sha256:" + "1" * 64,
        }
        with (
            mock.patch.object(
                engine_service.backtest_service,
                "validate_backtest_composition",
                return_value=validation,
            ) as validate,
            mock.patch.object(
                engine_service,
                "prepare_backtest_submission",
                return_value=prepared,
            ) as prepare,
        ):
            status, body = self.request(
                "POST", "/api/backtest-compositions/validate", {"raw": True}
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body, validation)
            validate.assert_called_once_with(self.config, {"raw": True})
            prepare.assert_not_called()

            status, body = self.request(
                "POST", "/api/backtest-submissions/prepare", {"raw": True}
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body, prepared)
            prepare.assert_called_once()
            args, kwargs = prepare.call_args
            self.assertEqual(args[0], self.config)
            self.assertEqual(args[1], {"raw": True})
            self.assertIs(
                args[2],
                engine_service.EngineServiceHandler.prepared_backtest_submissions,
            )
            self.assertRegex(kwargs["session_identity"], r"^[0-9a-f]{64}$")

    def test_prepared_backtest_submit_wrapper_is_exact_and_session_bound(self):
        manager = mock.Mock()
        manager.submit.return_value = {
            "jobId": "job_test",
            "backtestId": "bt_test",
        }
        previous = engine_service.EngineServiceHandler.backtest_job_manager
        engine_service.EngineServiceHandler.backtest_job_manager = manager
        try:
            payload = {
                "request": {"raw": True},
                "preparedSubmissionToken": "opaque",
            }
            status, body = self.request("POST", "/api/backtests", payload)
            self.assertEqual(status, 202, body)
            self.assertTrue(body["accepted"])
            manager.submit.assert_called_once()
            args, kwargs = manager.submit.call_args
            self.assertEqual(args, ({"raw": True},))
            self.assertEqual(kwargs["prepared_submission_token"], "opaque")
            self.assertRegex(kwargs["session_identity"], r"^[0-9a-f]{64}$")

            manager.reset_mock()
            status, body = self.request(
                "POST",
                "/api/backtests",
                {**payload, "executionSnapshot": {"forged": True}},
            )
            self.assertEqual(status, 400, body)
            self.assertIn("unsupported field", body["error"])
            manager.submit.assert_not_called()

            for token in (None, ""):
                with self.subTest(token=token):
                    status, body = self.request(
                        "POST",
                        "/api/backtests",
                        {
                            "request": {"raw": True},
                            "preparedSubmissionToken": token,
                        },
                    )
                    self.assertEqual(status, 400, body)
                    self.assertIn("non-empty string", body["error"])
            manager.submit.assert_not_called()
        finally:
            engine_service.EngineServiceHandler.backtest_job_manager = previous

    def test_get_query_contract_rejects_unknown_blank_repeated_and_invalid_values(self):
        status, body = self.request("GET", "/api/health?typo=")
        self.assertEqual(status, 400)
        self.assertIn("unsupported query field", body["error"])

        for target in (
            "/api/modules?limit=1&limit=2",
            "/api/modules?kind=Signal&kind=Rule",
            "/api/repositories?repository=modules&repository=data",
        ):
            status, body = self.request("GET", target)
            self.assertEqual(status, 400, body)
            self.assertIn("may only be supplied once", body["error"])

        for invalid_limit in ("", "-1", "+1", "1.0", "501"):
            status, body = self.request("GET", f"/api/modules?limit={invalid_limit}")
            self.assertEqual(status, 400, body)
            self.assertIn("limit", body["error"])

    def test_boolean_queries_accept_only_lowercase_true_or_false(self):
        for target in (
            "/api/backtests?includeArchived=1",
            "/api/backtests?includeArchived=yes",
            "/api/backtests?includeArchived=True",
            "/api/history?full=0",
            "/api/history?full=TRUE",
            "/api/history?full=",
        ):
            status, body = self.request("GET", target)
            self.assertEqual(status, 400, body)
            self.assertIn("true", body["error"])
            self.assertIn("false", body["error"])

        status, body = self.request(
            "GET", "/api/backtests?limit=0&includeArchived=false"
        )
        self.assertEqual(status, 200, body)
        status, body = self.request("GET", "/api/history?limit=0&full=true")
        self.assertEqual(status, 200, body)
        self.assertIn("pipelineStore", body)

    def test_result_get_paths_reject_empty_non_string_and_duplicate_values(self):
        for target in (
            "/api/backtests/missing/result?path=",
            "/api/backtests/missing/result?path=metrics&path=metrics",
        ):
            status, body = self.request("GET", target)
            self.assertEqual(status, 400, body)

        with self.assertRaisesRegex(engine_service.QueryValidationError, "must be a string"):
            engine_service.query_unique_nonempty_values({"path": [1]}, "path")

        self.assertEqual(
            engine_service.query_unique_nonempty_values(
                {"path": ["metrics", "executionChain"]}, "path"
            ),
            ["metrics", "executionChain"],
        )

    def test_result_view_and_complete_metadata_use_distinct_read_models(self):
        complete = {
            "backtestId": "bt_test",
            "status": "completed",
            "request": {"executionSnapshot": {"snapshotHash": "sha256:test"}},
        }
        view = {
            "backtestId": "bt_test",
            "status": "completed",
            "metrics": {"cycleCount": 1},
        }
        with (
            mock.patch.object(
                engine_service.result_repository,
                "get_backtest_meta",
                return_value=complete,
            ) as get_meta,
            mock.patch.object(
                engine_service.result_repository,
                "get_backtest_result_view",
                return_value=view,
            ) as get_view,
        ):
            status, body = self.request("GET", "/api/backtests/bt_test/view")
            self.assertEqual((status, body), (200, view))
            get_view.assert_called_once_with(self.config, "bt_test")
            get_meta.assert_not_called()

            status, body = self.request("GET", "/api/backtests/bt_test/meta")
            self.assertEqual((status, body), (200, complete))
            get_meta.assert_called_once_with(self.config, "bt_test")

    def test_post_and_delete_reject_even_blank_unsupported_query_fields(self):
        for target in (
            "/api/backtest-compositions/validate?typo=",
            "/api/backtest-submissions/prepare?typo=",
        ):
            status, body = self.request("POST", target, {})
            self.assertEqual(status, 400, body)
            self.assertIn("unsupported query field", body["error"])

    def test_logout_rejects_query_or_body_fields_without_ending_the_session(self):
        for target, payload, expected in (
            ("/auth/logout?unexpected=1", {}, "unsupported query field"),
            ("/auth/logout", {"unexpected": True}, "unsupported field"),
            ("/auth/logout", [], "must be an object"),
        ):
            with self.subTest(target=target, payload=payload):
                status, body = self.request("POST", target, payload)
                self.assertEqual(status, 400, body)
                self.assertIn(expected, body["error"])

        status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200, body)

        status, body = self.request(
            "DELETE", "/api/data/workspaces/missing?typo="
        )
        self.assertEqual(status, 400, body)
        self.assertIn("unsupported query field", body["error"])

    def test_config_loader_is_exact_and_preserves_supported_jupyter_fields(self):
        root = Path(self.temp.name)
        path = root / "strict-config.json"
        valid = {
            "liveRoot": str(root / "live-config"),
            "releaseRoot": str(root / "release-config"),
            "controlRoot": str(root / "control-config"),
            "allowInsecureAuth": False,
            "backtestMaxWorkers": 2,
            "jupyterHost": "127.0.0.1",
            "jupyterBaseUrl": "/jupyter/",
        }
        path.write_text(json.dumps(valid), encoding="utf-8")
        loaded = control.load_config(path)
        self.assertIs(loaded["allowInsecureAuth"], False)
        self.assertEqual(loaded["backtestMaxWorkers"], 2)
        self.assertEqual(loaded["jupyterHost"], "127.0.0.1")

        for invalid in (
            {key: value for key, value in valid.items() if key != "controlRoot"},
            {**valid, "allowInsecureAuth": "false"},
            {**valid, "backtestMaxWorkers": 0},
            {**valid, "jupyterPort": 18888},
            {**valid, "unknownOption": True},
        ):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ValueError):
                control.load_config(path)

    def test_config_loader_validates_mining_fields_without_coercion(self):
        root = Path(self.temp.name)
        path = root / "mining-config.json"
        valid = {
            "liveRoot": str(root / "live-mining-config"),
            "releaseRoot": str(root / "release-mining-config"),
            "controlRoot": str(root / "control-mining-config"),
            "miningRoot": str(root / "mining"),
            "miningAutoStart": False,
            "miningExposeTestProvider": False,
            "miningHttpTimeout": 20.5,
            "miningMaxPageBytes": 4096,
            "miningMaxPagesPerRun": 25,
            "miningStandbyRetrySeconds": 15,
        }
        path.write_text(json.dumps(valid), encoding="utf-8")
        loaded = control.load_config(path)
        self.assertIs(loaded["miningAutoStart"], False)
        self.assertEqual(loaded["miningHttpTimeout"], 20.5)
        self.assertEqual(loaded["miningMaxPagesPerRun"], 25)

        for field, value in (
            ("miningRoot", ""),
            ("miningAutoStart", "false"),
            ("miningExposeTestProvider", 0),
            ("miningHttpTimeout", True),
            ("miningHttpTimeout", float("inf")),
            ("miningMaxPageBytes", 4096.0),
            ("miningMaxPagesPerRun", True),
            ("miningStandbyRetrySeconds", "15"),
        ):
            with self.subTest(field=field, value=value):
                candidate = {**valid, field: value}
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(ValueError):
                    control.load_config(path)

    def test_pipeline_versions_are_archived_and_system_assigned(self):
        created = self.create_pipeline("Versioned Pipeline")
        pipeline_id = created["pipelineId"]
        self.assertEqual(created["version"], "1")
        draft = self.signal_draft(pipeline_id)
        status, saved = self.request("POST", f"/api/pipelines/{pipeline_id}/versions", draft)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["version"], "2")
        status, unchanged = self.request("POST", f"/api/pipelines/{pipeline_id}/versions", draft)
        self.assertEqual(status, 200, unchanged)
        self.assertEqual(unchanged["version"], "2")
        self.assertTrue(unchanged["unchanged"])
        status, detail = self.request("GET", f"/api/pipelines/{pipeline_id}")
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["pipeline"]["currentVersion"], "2")
        self.assertEqual([row["version"] for row in detail["versions"]], ["1", "2"])
        status, version_detail = self.request(
            "GET", f"/api/pipelines/{pipeline_id}/versions/1"
        )
        self.assertEqual(status, 200, version_detail)
        self.assertNotIn("attachment", version_detail)
        self.assertIn("definition", version_detail)
        status, _removed = self.request("GET", f"/api/pipelines/{pipeline_id}/revisions")
        self.assertEqual(status, 404)

    def test_removed_pipeline_lifecycle_routes_do_not_exist(self):
        status, _body = self.request("POST", "/api/pipeline-versions", {})
        self.assertEqual(status, 404)
        status, _body = self.request("POST", "/api/attach", {})
        self.assertEqual(status, 404)
        status, _body = self.request("POST", "/api/detach", {})
        self.assertEqual(status, 404)

    def test_pipeline_repository_rejects_a_missing_archived_version(self):
        pipeline_id = self.create_pipeline("Complete Version History")["pipelineId"]
        draft = self.signal_draft(pipeline_id)
        status, saved = self.request(
            "POST", f"/api/pipelines/{pipeline_id}/versions", draft
        )
        self.assertEqual(status, 200, saved)
        store = pipeline_repository.load_pipeline_store(self.config)
        del store["versions"][f"{pipeline_id}/1"]
        with self.assertRaisesRegex(ValueError, "not complete and monotonic"):
            pipeline_repository.validate_pipeline_store(self.config, store)

    def test_backend_enforces_stage_kind_ownership_and_removed_fields(self):
        pipeline_id = self.create_pipeline("Constrained Pipeline")["pipelineId"]
        draft = self.signal_draft(pipeline_id)
        misplaced = {
            **draft,
            "stages": {"inputs": ["sma"]},
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        }
        status, body = self.request("POST", f"/api/pipelines/{pipeline_id}/versions", misplaced)
        self.assertEqual(status, 400)
        self.assertIn("unsupported stage", body["error"])

        status, body = self.request(
            "POST", f"/api/pipelines/{pipeline_id}/versions", {**draft, "stages": {"signal": ["sma"]}}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported stage", body["error"])

        status, body = self.request(
            "POST",
            f"/api/pipelines/{pipeline_id}/versions",
            {**draft, "stages": {"target": ["first", "second"]}},
        )
        self.assertEqual(status, 400)
        self.assertIn("at most one", body["error"])

        status, body = self.request(
            "POST", f"/api/pipelines/{pipeline_id}/versions", {**draft, "version": "99"}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported field", body["error"])

        status, body = self.request(
            "POST", f"/api/pipelines/{pipeline_id}/versions", {**draft, "analysis": {}}
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported field", body["error"])

    def test_analysis_environment_and_pipeline_are_independent_resources(self):
        pipeline_id = self.create_pipeline("Independent Pipeline")["pipelineId"]
        status, analyses = self.request("GET", "/api/analyses")
        self.assertEqual(status, 200, analyses)
        status, environments = self.request("GET", "/api/environments")
        self.assertEqual(status, 200, environments)
        status, pipeline = self.request("GET", f"/api/pipelines/{pipeline_id}")
        self.assertEqual(status, 200, pipeline)
        self.assertNotIn("analysis", pipeline["definition"])
        self.assertNotIn("environment", pipeline["definition"])
        self.assertGreaterEqual(analyses["total"], 1)
        self.assertGreaterEqual(environments["total"], 1)

    def test_graph_resource_api_does_not_synthesize_required_fields(self):
        status, body = self.request("POST", "/api/analyses", {})
        self.assertEqual(status, 400)
        self.assertIn("analysisId is required", body["error"])
        status, body = self.request("POST", "/api/environments", {
            "schemaVersion": 1,
            "environmentId": "wrong-schema-environment",
            "name": "Wrong Schema Environment",
            "instances": {},
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        })
        self.assertEqual(status, 400)
        self.assertIn("schemaVersion 2", body["error"])

    def test_disable_is_not_archive_and_versions_remain_readable(self):
        pipeline_id = self.create_pipeline("Disable Pipeline")["pipelineId"]
        status, disabled = self.request(
            "POST", f"/api/pipelines/{pipeline_id}/disable", {"reason": "done"}
        )
        self.assertEqual(status, 200, disabled)
        self.assertEqual(disabled["pipeline"]["status"], "inactive")
        status, versions = self.request("GET", f"/api/pipelines/{pipeline_id}/versions")
        self.assertEqual(status, 200, versions)
        self.assertEqual([row["version"] for row in versions["versions"]], ["1"])
        status, body = self.request(
            "POST",
            f"/api/pipelines/{pipeline_id}/versions",
            self.signal_draft(pipeline_id),
        )
        self.assertEqual(status, 400)
        self.assertIn("read-only", body["error"])

    def test_module_repository_endpoint_rejects_cross_repository_kind(self):
        status, body = self.request("POST", "/api/modules", {
            "kind": "Analyzer",
            "moduleId": "wrong-repository",
        })
        self.assertEqual(status, 400)
        self.assertIn("Invalid Pipeline module kind", body["error"])


if __name__ == "__main__":
    unittest.main()
