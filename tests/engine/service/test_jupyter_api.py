"""Authenticated Jupyter service API integration tests."""

import http.client
import json
import secrets
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dataset_adapters import ohlcv
from engine.core import clock as engine_clock
import engine_service
from engine.service import jupyter_workspaces
from engine.control import database as engine_database
from engine.control import auth as trade_auth
from engine.contracts import sampler as sampler_contracts
from engine.repository import samplers
from engine.repository import dataset_workspaces

class EngineJupyterApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "allowInsecureAuth": True,
        }
        engine_database.prepare_database(self.config)
        ohlcv.register_dataset(
            self.config,
            dataset_id="source",
            name="source",
            symbol="SPY",
            source="test",
            interval="d",
            rows=[{"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            availability_policy="bar_end_utc",
        )
        self.workspace = dataset_workspaces.create_workspace(self.config, {
            "workspaceId": "jupyter-api",
            "sources": [{"datasetId": "source", "alias": "dataset1"}],
        })
        Path(self.workspace["workspacePath"], "prepare.py").write_text(
            "from pathlib import Path\nPath('result.txt').write_text('ok')\n",
            encoding="utf-8",
        )
        samplers.save_sampler(self.config, {
            "samplerId": "jupyter-sampler",
            "name": "Jupyter Sampler",
            "type": "row-map",
            "config": {
                "mapping": {"price.close": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            },
            "parameterSchema": sampler_contracts.infer_sampler_parameter_schema({
                "mapping": {"price.close": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            }),
            "outputSchema": {"price.close": {"type": "number"}},
            "source": "",
            "entryPoint": "",
        })
        samplers.save_sampler(self.config, {
            "samplerId": "nested-row",
            "name": "Engine-owned row Sampler fixture",
            "type": "row-map",
            "config": {
                "mapping": {"price.close": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            },
            "parameterSchema": sampler_contracts.infer_sampler_parameter_schema({
                "mapping": {"price.close": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            }),
            "outputSchema": {"price.close": {"type": "number"}},
            "source": "",
            "entryPoint": "",
        }, engine_owned=True)
        engine_service.EngineServiceHandler.config = self.config
        trade_auth.ensure_default_user(self.config)
        self.password = secrets.token_urlsafe(24)
        with trade_auth.connect(self.config) as connection:
            now = engine_clock.utc_now()
            connection.execute(
                """
                INSERT INTO users
                (user_id, email, password_hash, role, status, created_at, updated_at)
                VALUES ('jupyter-test-user', 'jupyter-test@example.invalid', ?, 'admin', 'active', ?, ?)
                """,
                (trade_auth.hash_password(self.password), now, now),
            )
            connection.commit()
        login = trade_auth.login(self.config, "jupyter-test@example.invalid", self.password, "127.0.0.1")
        self.auth_headers = {
            "Cookie": (
                f"{trade_auth.SESSION_COOKIE}={login['token']}; "
                f"{trade_auth.CSRF_COOKIE}={login['csrfToken']}"
            ),
            "X-CSRF-Token": login["csrfToken"],
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), engine_service.EngineServiceHandler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        engine_service.EngineServiceHandler.public_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        jupyter_workspaces.stop_managed_process()
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = dict(self.auth_headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), content

    def test_workspace_scripts_open_api_and_jupyter_proxy(self):
        status, _, content = self.request("GET", "/api/data/workspaces/jupyter-api/scripts")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["scripts"][0]["path"], "prepare.py")

        status, _, content = self.request("POST", "/api/data/workspaces/jupyter-api/jupyter", {})
        self.assertEqual(status, 200)
        opened = json.loads(content)
        self.assertTrue(opened["accepted"])
        opened_url = urlsplit(opened["url"])
        self.assertRegex(opened_url.path, r"^/jupyter/w/dataset-jupyter-api-[a-f0-9]+/lab$")
        self.assertEqual(opened_url.query, "reset")
        self.assertTrue(opened["jupyter"]["running"])

        status, _, _ = self.request("GET", opened_url.path + "?" + opened_url.query)
        self.assertEqual(status, 200)

    def test_custom_sampler_opens_isolated_editor_and_builtin_is_rejected(self):
        with (
            mock.patch.object(
                engine_service.jupyter_workspaces,
                "workspace_url",
                return_value="http://127.0.0.1/jupyter/w/sampler-test/lab?reset",
            ) as workspace_url,
            mock.patch.object(
                engine_service.jupyter_workspaces,
                "status",
                return_value={"running": True},
            ),
        ):
            status, _, content = self.request(
                "POST", "/api/data/samplers/jupyter-sampler/versions/1/jupyter", {}
            )
        self.assertEqual(status, 200)
        opened = json.loads(content)
        self.assertTrue(opened["accepted"])
        self.assertEqual(opened["sourceSamplerKey"], "jupyter-sampler::1")
        self.assertTrue(Path(opened["workspacePath"], "sampler.json").is_file())
        workspace_url.assert_called_once_with(
            self.config,
            engine_service.EngineServiceHandler.public_url,
            opened["workspaceId"],
            workspace_kind="sampler",
        )

        status, _, content = self.request(
            "POST", "/api/data/samplers/nested-row/versions/1/jupyter", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("read-only", json.loads(content)["error"])

    def test_no_parameter_jupyter_and_publish_routes_reject_request_fields_first(self):
        routes = (
            "/api/data/workspaces/missing/jupyter",
            "/api/data/samplers/missing/versions/1/jupyter",
            "/api/data/samplers/missing/versions/1/publish",
            "/api/modules/Signal/missing/versions/1/jupyter",
            "/api/modules/Signal/missing/versions/1/publish",
            "/api/analysis-modules/Analyzer/missing/versions/1/jupyter",
            "/api/analysis-modules/Analyzer/missing/versions/1/publish",
            "/api/environment-modules/Account/missing/versions/1/jupyter",
            "/api/environment-modules/Account/missing/versions/1/publish",
        )
        for route in routes:
            with self.subTest(route=route):
                status, _, content = self.request("POST", route, {"unexpected": True})
                self.assertEqual(status, 400, content)
                self.assertIn("unsupported field", json.loads(content)["error"])

        status, _, content = self.request(
            "POST", "/api/data/workspaces/missing/jupyter", []
        )
        self.assertEqual(status, 400, content)
        self.assertIn("must be an object", json.loads(content)["error"])


if __name__ == "__main__":
    unittest.main()
