"""Authenticated Engine HTTP boundary tests for the Mining application seam."""

import http.client
import json
import secrets
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from engine.control import auth as trade_auth
from engine.core import clock as engine_clock
import engine_service
from mining.api import DisabledMiningApi, MiningApi


class EngineMiningApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.config = {
            "controlRoot": str(cls.root / "control"),
            "releaseRoot": str(cls.root / "releases"),
            "liveRoot": str(cls.root / "live"),
            "miningRoot": str(cls.root / "mining"),
            "miningExposeTestProvider": True,
            "allowInsecureAuth": True,
        }
        password = secrets.token_urlsafe(24)
        with trade_auth.connect(cls.config) as connection:
            now = engine_clock.utc_now()
            connection.execute(
                """INSERT INTO users(
                    user_id,email,password_hash,role,status,created_at,updated_at
                ) VALUES(
                    'mining-api-user','mining-api@example.invalid',?,
                    'admin','active',?,?
                )""",
                (trade_auth.hash_password(password), now, now),
            )
        login = trade_auth.login(
            cls.config,
            "mining-api@example.invalid",
            password,
            "127.0.0.1",
        )
        cls.session_cookie = f"{trade_auth.SESSION_COOKIE}={login['token']}"
        cls.csrf_cookie = f"{trade_auth.CSRF_COOKIE}={login['csrfToken']}"
        cls.csrf_token = login["csrfToken"]

        cls.previous_config = engine_service.EngineServiceHandler.config
        cls.previous_public_url = engine_service.EngineServiceHandler.public_url
        cls.previous_mining_api = engine_service.EngineServiceHandler.mining_api
        engine_service.EngineServiceHandler.config = cls.config
        engine_service.EngineServiceHandler.public_url = "http://127.0.0.1"
        engine_service.EngineServiceHandler.mining_api = MiningApi(cls.config)
        engine_service.EngineServiceHandler.stopping.clear()
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), engine_service.EngineServiceHandler
        )
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        engine_service.EngineServiceHandler.config = cls.previous_config
        engine_service.EngineServiceHandler.public_url = cls.previous_public_url
        engine_service.EngineServiceHandler.mining_api = cls.previous_mining_api
        engine_service.EngineServiceHandler.stopping.clear()
        cls.temporary.cleanup()

    def setUp(self):
        engine_service.EngineServiceHandler.config = self.config
        engine_service.EngineServiceHandler.mining_api = MiningApi(self.config)

    def request(self, method, path, *, payload=None, headers=None, raw_body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if raw_body is not None:
            body = raw_body
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
        else:
            body = None
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content or b"{}")

    def authenticated_headers(self, *, csrf=True, csrf_token=None):
        headers = {
            "Cookie": f"{self.session_cookie}; {self.csrf_cookie}",
        }
        if csrf:
            headers["X-CSRF-Token"] = (
                self.csrf_token if csrf_token is None else csrf_token
            )
        return headers

    def test_unauthenticated_get_is_rejected_before_mining(self):
        status, body = self.request("GET", "/api/mining/jobs")

        self.assertEqual(status, 401, body)
        self.assertIn("Authentication", body["error"])

    def test_post_without_or_with_forged_csrf_is_rejected(self):
        payload = {
            "jobId": "csrf-rejected-job",
            "name": "Must not be created",
            "provider": "deterministic-fake",
            "providerConfig": {
                "records": [[0, "native"]],
                "identityPointer": "/0",
                "eventTimePointer": "/0",
            },
        }
        for headers in (
            self.authenticated_headers(csrf=False),
            self.authenticated_headers(csrf_token="forged-token"),
        ):
            with self.subTest(headers=headers):
                status, body = self.request(
                    "POST", "/api/mining/jobs", payload=payload, headers=headers
                )
                self.assertEqual(status, 403, body)
                self.assertIn("CSRF", body["error"])
        job_ids = {
            job["jobId"]
            for job in engine_service.EngineServiceHandler.mining_api.store.list_jobs()
        }
        self.assertNotIn("csrf-rejected-job", job_ids)

    def test_disabled_mode_does_not_create_a_mining_directory(self):
        disabled_root = self.root / "disabled-mining"
        disabled_config = dict(self.config)
        disabled_config.pop("miningRoot")
        engine_service.EngineServiceHandler.config = disabled_config
        engine_service.EngineServiceHandler.mining_api = DisabledMiningApi()

        status, body = self.request(
            "GET",
            "/api/mining/health",
            headers=self.authenticated_headers(csrf=False),
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "disabled")
        self.assertFalse(disabled_root.exists())

    def test_strict_query_and_body_errors_are_400_not_500(self):
        get_targets = (
            "/api/mining/jobs?unexpected=1",
            "/api/mining/jobs?limit=1&limit=2",
            "/api/mining/jobs?limit=0",
        )
        for target in get_targets:
            with self.subTest(target=target):
                status, body = self.request(
                    "GET", target, headers=self.authenticated_headers(csrf=False)
                )
                self.assertEqual(status, 400, body)
                self.assertIn("error", body)

    def test_missing_job_subresources_are_http_not_found(self):
        for resource in ("records", "gaps"):
            with self.subTest(resource=resource):
                status, body = self.request(
                    "GET",
                    f"/api/mining/jobs/missing-http-job/{resource}",
                    headers=self.authenticated_headers(csrf=False),
                )
                self.assertEqual(status, 404, body)
                self.assertIn("does not exist", body["error"])

    def test_explicit_non_object_provider_config_is_http_bad_request(self):
        base = {
            "jobId": "invalid-http-provider-config",
            "name": "Invalid provider config",
            "provider": "deterministic-fake",
        }
        for provider_config in (None, False, []):
            with self.subTest(provider_config=provider_config):
                status, body = self.request(
                    "POST",
                    "/api/mining/jobs",
                    payload={**base, "providerConfig": provider_config},
                    headers=self.authenticated_headers(),
                )
                self.assertEqual(status, 400, body)
                self.assertIn("providerConfig", body["error"])

        invalid_posts = (
            ({"unexpected": True}, None),
            (
                {
                    "jobId": "invalid-number-job",
                    "name": "Invalid number",
                    "provider": "deterministic-fake",
                    "providerConfig": {
                        "records": [[0]],
                        "identityPointer": "/0",
                        "eventTimePointer": "/0",
                    },
                    "scheduleSeconds": 60.0,
                },
                None,
            ),
            (None, b'{"duplicate":1,"duplicate":2}'),
        )
        for payload, raw_body in invalid_posts:
            with self.subTest(payload=payload, raw_body=raw_body):
                status, body = self.request(
                    "POST",
                    "/api/mining/jobs",
                    payload=payload,
                    raw_body=raw_body,
                    headers=self.authenticated_headers(),
                )
                self.assertEqual(status, 400, body)
                self.assertIn("error", body)

    def test_authenticated_session_routes_to_real_mining_api(self):
        payload = {
            "jobId": "http-boundary-job",
            "name": "HTTP boundary",
            "provider": "deterministic-fake",
            "providerConfig": {
                "records": [[0, {"native": True}]],
                "identityPointer": "/0",
                "eventTimePointer": "/0",
            },
            "scheduleSeconds": 60,
            "overlapRecords": 0,
            "continuityStep": 1,
        }
        status, created = self.request(
            "POST",
            "/api/mining/jobs",
            payload=payload,
            headers=self.authenticated_headers(),
        )
        self.assertEqual(status, 201, created)
        self.assertEqual(created["job"]["jobId"], "http-boundary-job")
        self.assertNotIn("providerConfig", created["job"])

        status, detail = self.request(
            "GET",
            "/api/mining/jobs/http-boundary-job",
            headers=self.authenticated_headers(csrf=False),
        )
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["job"]["provider"], "deterministic-fake")
        self.assertNotIn("providerConfig", detail["job"])


if __name__ == "__main__":
    unittest.main()
