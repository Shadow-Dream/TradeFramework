import json
import random
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import httpx

from mining.http_client import RobustHttpClient
from mining.__main__ import load_create_payload, main as mining_cli_main
from mining.api import DisabledMiningApi, MiningApi
from mining.config import load_config
from mining.providers import enabled_provider_ids, get_provider
from mining.providers.base import RetryableProviderError, canonical_json, record_entries
from mining.store import MiningStore
from mining.worker import MiningWorker, SingleWriterLock, WorkerAlreadyRunning
from mining.supervisor import MiningSupervisor


class MiningTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "miningRoot": str(root / "mining"),
            "miningHttpTimeout": 1,
        }
        self.store = MiningStore(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def create_fake(self, provider_config=None, **overrides):
        provider = get_provider("deterministic-fake")
        config = provider.validate_config(provider_config or {
            "records": [[0, "native-a"], [60, "native-b"], [120, "native-c"]],
            "pageSize": 2,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        })
        values = {
            "job_id": "fake-minute-job",
            "name": "Provider native fixture",
            "provider": provider.provider_id,
            "provider_config": config,
            "initial_cursor": provider.initial_cursor(config),
            "schedule_seconds": 3600,
            "overlap_records": 1,
            "continuity_step": 60,
        }
        values.update(overrides)
        return self.store.create_job(**values)

    def run_once(self):
        return MiningWorker(
            {**self.config, "miningExposeTestProvider": True},
            worker_id="test-worker",
            poll_seconds=0.01,
            random_source=random.Random(0),
            store=self.store,
        ).run(once=True)

    def exact_cli_config(self):
        root = Path(self.temporary.name)
        return {
            "liveRoot": str(root / "live"),
            "releaseRoot": str(root / "releases"),
            "controlRoot": str(root / "control"),
            "miningRoot": str(root / "mining-cli"),
            "miningAutoStart": False,
            "miningExposeTestProvider": False,
            "miningHttpTimeout": 20,
            "miningMaxPageBytes": 67108864,
            "miningMaxPagesPerRun": 25,
        }

    def test_standalone_config_requires_the_exact_current_complete_shape(self):
        path = Path(self.temporary.name) / "mining-config.json"
        exact = self.exact_cli_config()
        path.write_text(json.dumps(exact), encoding="utf-8")
        self.assertEqual(load_config(path), {**exact, "miningHttpTimeout": 20.0})

        for invalid in (
            {key: value for key, value in exact.items() if key != "controlRoot"},
            {**exact, "unknownField": True},
            {**exact, "miningHttpTimeout": True},
            {**exact, "miningMaxPagesPerRun": 25.0},
            {**exact, "miningStandbyRetrySeconds": 15},
        ):
            with self.subTest(fields=sorted(invalid)):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_standalone_config_rejects_duplicate_fields(self):
        path = Path(self.temporary.name) / "mining-duplicate-config.json"
        path.write_text(
            '{"liveRoot":"/live","liveRoot":"/other"}', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: liveRoot"):
            load_config(path)

    def test_standalone_worker_lock_contention_exits_73(self):
        worker = mock.Mock()
        worker.run.side_effect = WorkerAlreadyRunning("writer is active")
        with (
            mock.patch("mining.__main__.load_config", return_value=self.exact_cli_config()),
            mock.patch("mining.__main__.MiningStore", return_value=self.store),
            mock.patch("mining.__main__.MiningWorker", return_value=worker),
            mock.patch(
                "sys.argv",
                ["mining", "--config", "/exact/mining-config.json", "worker"],
            ),
        ):
            with self.assertRaises(SystemExit) as raised:
                mining_cli_main()
        self.assertEqual(raised.exception.code, 73)

    def test_fake_provider_paginates_and_preserves_native_records(self):
        self.create_fake()
        self.assertEqual(self.run_once(), 1)
        job = self.store.get_job("fake-minute-job")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["currentRecords"], 3)
        self.assertEqual(job["pageCount"], 3)  # includes the empty terminal page
        records = self.store.list_records("fake-minute-job")
        self.assertEqual([row["record"] for row in reversed(records)], [
            [0, "native-a"], [60, "native-b"], [120, "native-c"]
        ])
        manifest = self.store.manifest("fake-minute-job")
        self.assertEqual(manifest["recordContract"], "provider-native; no Engine field normalization")
        for page in manifest["pages"]:
            raw = self.store.root / page["raw"]["path"]
            self.assertEqual(page["raw"]["sha256"], __import__("hashlib").sha256(raw.read_bytes()).hexdigest())

    def test_overlap_is_idempotent_and_changed_provider_record_is_revisioned(self):
        self.create_fake({
            "records": [[0, "v1"], [60, "same"]],
            "attemptRecords": {"2": [[0, "v2"], [60, "same"]]},
            "pageSize": 10,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=10)
        self.run_once()
        self.store.run_now("fake-minute-job")
        self.run_once()
        job = self.store.get_job("fake-minute-job")
        self.assertEqual(job["currentRecords"], 2)
        self.assertGreater(job["observations"], job["currentRecords"])
        self.assertEqual(job["revisions"], 1)
        by_identity = {row["identity"]: row for row in self.store.list_records("fake-minute-job")}
        self.assertEqual(by_identity[0]["record"], [0, "v2"])
        self.assertEqual(by_identity[0]["revision"], 2)

    def test_retry_wait_is_persistent_then_resume_succeeds(self):
        self.create_fake({
            "records": [[0, "ok"]],
            "pageSize": 2,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
            "failAttempts": [1],
            "retryAfter": 0,
        })
        self.run_once()
        failed = self.store.get_job("fake-minute-job")
        self.assertEqual(failed["status"], "retry_wait")
        self.assertEqual(failed["consecutiveFailures"], 1)
        self.store.run_now("fake-minute-job")
        self.run_once()
        recovered = self.store.get_job("fake-minute-job")
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["currentRecords"], 1)

    def test_invalid_provider_contract_is_persistently_blocked(self):
        self.create_fake({
            "records": [[0, "unused"]],
            "identityPointer": "/0",
            "eventTimePointer": "/0",
            "invalidPageAttempts": [1],
        })
        self.run_once()
        job = self.store.get_job("fake-minute-job")
        self.assertEqual(job["status"], "blocked")
        self.assertIn("must be a list", job["blockedReason"])

    def test_crash_between_file_rename_and_db_commit_does_not_advance_cursor(self):
        job = self.create_fake({
            "records": [[0, "native"]],
            "pageSize": 1,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=0)
        owner = "crash-worker"
        claimed = self.store.claim_next(
            owner,
            lease_seconds=5,
            allowed_providers=enabled_provider_ids(include_test=True),
        )
        provider = get_provider(claimed["provider"])
        cursor = claimed["cursor"]
        self.store.begin_active(claimed["jobId"], owner, lane="main", cursor=cursor, refill_id=None)
        self.store.transition(claimed["jobId"], owner, "fetching")
        config = claimed["providerConfig"]
        page = provider().fetch_page(cursor, {**config, "_runtimeAttempt": 1}, None)
        next_cursor = provider.next_cursor(cursor, page, config)
        self.store.transition(claimed["jobId"], owner, "committing")
        with self.assertRaisesRegex(RuntimeError, "Injected crash"):
            self.store.commit_page(
                job_id=claimed["jobId"], owner=owner, lane="main", refill_id=None,
                request_cursor=cursor, next_cursor=next_cursor, raw=page.raw,
                response_status=page.status_code, response_headers=page.headers,
                source=page.source, entries=record_entries(provider, page.records, config),
                fault_stage="after_files",
            )
        after_crash = self.store.get_job(claimed["jobId"])
        self.assertEqual(after_crash["cursor"], job["cursor"])
        self.assertEqual(after_crash["pageCount"], 0)
        self.store.recover_expired_leases(now=time.time() + 100)
        self.assertEqual(self.store.recover_orphans(), 2)
        self.store.run_now(claimed["jobId"])
        self.run_once()
        self.assertEqual(self.store.get_job(claimed["jobId"])["currentRecords"], 1)

    def test_gap_is_detected_and_refill_task_is_durable(self):
        self.create_fake({
            "records": [[0, "a"], [60, "b"], [180, "d"]],
            "pageSize": 10,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=0)
        self.run_once()
        gaps = self.store.list_gaps("fake-minute-job")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missingStart"], 120)
        provider = get_provider("deterministic-fake")
        cursor = provider.cursor_for_gap(
            self.store.get_job("fake-minute-job", internal=True)["providerConfig"],
            gaps[0]["missingStart"], gaps[0]["missingEnd"],
        )
        refill = self.store.queue_refill("fake-minute-job", gaps[0]["gapId"], cursor)
        duplicate = self.store.queue_refill("fake-minute-job", gaps[0]["gapId"], cursor)
        self.assertEqual(refill["refillId"], duplicate["refillId"])

    def test_refill_queued_during_main_commit_is_not_delayed_by_schedule(self):
        self.create_fake({
            "records": [[0, "a"], [120, "c"]],
            "pageSize": 10,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=0, schedule_seconds=31_536_000)
        self.run_once()
        gap = self.store.list_gaps("fake-minute-job")[0]
        provider = get_provider("deterministic-fake")
        provider_config = self.store.get_job(
            "fake-minute-job", internal=True
        )["providerConfig"]

        self.store.run_now("fake-minute-job")
        owner = "active-main-worker"
        claimed = self.store.claim_next(
            owner, allowed_providers=enabled_provider_ids(include_test=True)
        )
        cursor = claimed["cursor"]
        self.store.begin_active(
            claimed["jobId"],
            owner,
            lane="main",
            cursor=cursor,
            refill_id=None,
        )
        refill_cursor = provider.cursor_for_gap(
            provider_config, gap["missingStart"], gap["missingEnd"]
        )
        refill = self.store.queue_refill(
            claimed["jobId"], gap["gapId"], refill_cursor
        )
        page = provider().fetch_page(
            cursor,
            {**provider_config, "_runtimeAttempt": claimed["attemptCount"]},
            None,
        )
        next_cursor = provider.next_cursor(cursor, page, provider_config)
        self.store.transition(claimed["jobId"], owner, "fetching")
        self.store.transition(claimed["jobId"], owner, "committing")
        committed_at = time.time()
        self.store.commit_page(
            job_id=claimed["jobId"],
            owner=owner,
            lane="main",
            refill_id=None,
            request_cursor=cursor,
            next_cursor=next_cursor,
            raw=page.raw,
            response_status=page.status_code,
            response_headers=page.headers,
            source=page.source,
            entries=record_entries(provider, page.records, provider_config),
            continue_fetch=False,
        )

        job = self.store.get_job(claimed["jobId"])
        self.assertEqual(job["status"], "queued")
        self.assertLessEqual(job["nextRunAt"], time.time())
        self.assertGreaterEqual(job["nextRunAt"], committed_at)
        next_claim = self.store.claim_next(
            "refill-worker", allowed_providers=enabled_provider_ids(include_test=True)
        )
        self.assertEqual(next_claim["refill"]["refill_id"], refill["refillId"])

    def test_incremental_gap_update_resolves_only_local_gap(self):
        self.create_fake({
            "records": [[0, "a"], [120, "c"], [180, "d"], [240, "e"], [300, "f"], [360, "g"], [480, "i"]],
            "attemptRecords": {"2": [[0, "a"], [60, "b"], [120, "c"], [180, "d"], [240, "e"], [300, "f"], [360, "g"], [480, "i"]]},
            "pageSize": 20, "identityPointer": "/0", "eventTimePointer": "/0",
        }, overlap_records=100, continuity_step=60)
        self.run_once()
        self.assertEqual(
            [(gap["previousEventTime"], gap["nextEventTime"]) for gap in self.store.list_gaps("fake-minute-job")],
            [(0, 120), (360, 480)],
        )
        self.store.run_now("fake-minute-job")
        self.run_once()
        open_gaps = self.store.list_gaps("fake-minute-job")
        self.assertEqual(
            [(gap["previousEventTime"], gap["nextEventTime"]) for gap in open_gaps],
            [(360, 480)],
        )
        resolved = self.store.list_gaps("fake-minute-job", include_resolved=True)
        local = next(gap for gap in resolved if gap["previousEventTime"] == 0)
        self.assertEqual(local["status"], "resolved")

    def test_single_writer_lock_rejects_second_worker(self):
        lock_path = self.store.root / "worker.lock"
        with SingleWriterLock(lock_path):
            with self.assertRaises(WorkerAlreadyRunning):
                with SingleWriterLock(lock_path):
                    pass

    def test_http_429_uses_retry_after_without_sleeping(self):
        def handler(request):
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        with RobustHttpClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(RetryableProviderError) as raised:
                client.get("https://example.test/page")
        self.assertEqual(raised.exception.retry_after, 7)

    def test_binance_adapter_preserves_official_raw_array(self):
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({
            "symbol": "BTCUSDT", "interval": "1m", "startTime": 0, "limit": 1000,
        })
        raw = b'[[0,"1","2","0.5","1.5","9",59999,"13",2,"4","6","0"]]'

        def handler(request):
            self.assertEqual(request.url.path, "/api/v3/klines")
            self.assertEqual(request.url.params["symbol"], "BTCUSDT")
            return httpx.Response(200, content=raw, request=request)

        with RobustHttpClient(transport=httpx.MockTransport(handler)) as client:
            page = provider().fetch_page(provider.initial_cursor(config), config, client)
        provider.validate_page(page, config)
        self.assertEqual(page.raw, raw)
        self.assertEqual(page.records[0][1], "1")
        self.assertEqual(provider.record_identity(page.records[0], config), 0)
        self.assertIsNotNone(provider.next_cursor(provider.initial_cursor(config), page, config))
        self.assertFalse(provider.should_continue(page, provider.next_cursor({}, page, config), config))

    def test_binance_rejects_nonfinite_response_before_any_commit(self):
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({
            "symbol": "BTCUSDT", "interval": "1m", "startTime": 0,
        })
        self.store.create_job(
            job_id="nonfinite-response-job",
            name="Nonfinite response",
            provider=provider.provider_id,
            provider_config=config,
            initial_cursor=provider.initial_cursor(config),
            schedule_seconds=3600,
            overlap_records=0,
            continuity_step=60_000,
        )
        worker = MiningWorker(
            self.config,
            worker_id="nonfinite-worker",
            random_source=random.Random(0),
            store=self.store,
        )

        class NonfiniteClient:
            def __init__(client_self, *_args, **_kwargs):
                pass

            def __enter__(client_self):
                return client_self

            def __exit__(client_self, *_args):
                return None

            def get(client_self, _url, *, params=None):
                from mining.providers.base import FetchPage
                return FetchPage(
                    raw=b'[[0,"1","2","0.5","1.5",NaN,59999]]',
                    payload=None,
                    records=(),
                    source="https://api.binance.com/api/v3/klines",
                )

        with mock.patch("mining.worker.RobustHttpClient", NonfiniteClient):
            self.assertEqual(worker.run(once=True), 1)

        job = self.store.get_job("nonfinite-response-job")
        self.assertEqual(job["status"], "blocked")
        self.assertIn("non-finite", job["blockedReason"])
        self.assertEqual((job["pageCount"], job["currentRecords"]), (0, 0))

    def test_binance_response_rejects_duplicate_keys_and_canonical_nan(self):
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({"symbol": "BTCUSDT", "interval": "1m"})

        def handler(request):
            return httpx.Response(200, content=b'{"a":1,"a":2}', request=request)

        with RobustHttpClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                provider().fetch_page({}, config, client)
        with self.assertRaises(ValueError):
            canonical_json({"value": float("nan")})

    def test_cli_create_payload_uses_strict_json_decoder(self):
        for name, content, error in (
            ("duplicate.json", b'{"name":"a","name":"b"}', "duplicate JSON key"),
            ("nan.json", b'{"value":NaN}', "non-finite JSON number"),
            ("overflow.json", b'{"value":1e400}', "non-finite JSON number"),
        ):
            with self.subTest(name=name):
                path = Path(self.temporary.name) / name
                path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, error):
                    load_create_payload(path)

    def test_store_create_job_rejects_coercion_and_nonfinite_json(self):
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({"symbol": "BTCUSDT", "interval": "1m"})
        base = {
            "job_id": "strict-store-job",
            "name": "Strict store",
            "provider": provider.provider_id,
            "provider_config": config,
            "initial_cursor": {},
            "schedule_seconds": 60,
            "overlap_records": 0,
            "continuity_step": None,
        }
        invalid = (
            ("job_id", 123),
            ("job_id", "UPPERCASE-JOB"),
            ("name", False),
            ("provider", 123),
            ("provider_config", []),
            ("provider_config", {"value": float("nan")}),
            ("provider_config", {1: "coerced-key"}),
            ("initial_cursor", {"offset": float("inf")}),
            ("initial_cursor", (0,)),
            ("schedule_seconds", True),
            ("schedule_seconds", 60.0),
            ("overlap_records", False),
            ("overlap_records", 0.0),
            ("continuity_step", True),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    self.store.create_job(**{**base, field: value})
        self.assertEqual(self.store.list_jobs(), [])

    def test_record_entries_rejects_nonfinite_numeric_event_time(self):
        class NonfiniteEventProvider:
            @classmethod
            def record_identity(cls, record, config):
                return record[0]

            @classmethod
            def event_time(cls, record, config):
                return record[1]

            @classmethod
            def is_final(cls, record, config):
                return True

        for value in (float("nan"), float("inf"), 10**1000):
            with self.subTest(value=str(value)[:20]):
                with self.assertRaisesRegex(ValueError, "non-finite numeric event time"):
                    record_entries(NonfiniteEventProvider, [["id", value]], {})

    def test_binance_config_rejects_implicit_type_coercion(self):
        provider = get_provider("binance-spot-klines")
        base = {"symbol": "BTCUSDT", "interval": "1m"}
        invalid_values = (
            ("symbol", 123),
            ("symbol", None),
            ("interval", 1),
            ("limit", True),
            ("limit", 1.5),
            ("limit", "1000"),
            ("startTime", True),
            ("startTime", 1.5),
            ("startTime", "0"),
            ("startTime", None),
            ("endTime", False),
            ("endTime", 1.5),
            ("endTime", "60000"),
            ("endTime", None),
            ("baseUrl", 123),
            ("baseUrl", None),
            ("requestIntervalSeconds", True),
            ("requestIntervalSeconds", "0.35"),
            ("requestIntervalSeconds", None),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "Binance"):
                    provider.validate_config({**base, field: value})

    def test_strict_api_creates_provider_native_job(self):
        api = MiningApi({**self.config, "miningExposeTestProvider": True})
        status, result = api.handle_post("/api/mining/jobs", {
            "jobId": "api-fake-job",
            "name": "API fake",
            "provider": "deterministic-fake",
            "providerConfig": {
                "records": [{"when": 1, "untouched": {"value": "x"}}],
                "identityPointer": "/when",
                "eventTimePointer": "/when",
            },
            "scheduleSeconds": 30,
            "overlapRecords": 2,
            "continuityStep": 1,
        })
        self.assertEqual(status, 201)
        self.assertEqual(result["job"]["provider"], "deterministic-fake")
        with self.assertRaisesRegex(ValueError, "Unknown mining request fields"):
            api.handle_post("/api/mining/jobs", {"name": "bad", "engineDataset": {}})

    def test_hidden_test_provider_cannot_be_used_to_create_a_job(self):
        api = MiningApi(self.config)
        with self.assertRaises(ValueError):
            api.handle_post("/api/mining/jobs", {
                "jobId": "hidden-fake-job",
                "name": "Hidden fake",
                "provider": "deterministic-fake",
                "providerConfig": {
                    "records": [[0, "native"]],
                    "identityPointer": "/0",
                    "eventTimePointer": "/0",
                },
            })
        self.assertEqual(self.store.list_jobs(), [])

    def test_existing_hidden_test_provider_job_is_blocked_before_claim(self):
        self.create_fake({
            "records": [[0, "first"], [120, "third"]],
            "pageSize": 10,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=0)
        self.run_once()
        before = self.store.get_job("fake-minute-job")
        gap = self.store.list_gaps("fake-minute-job")[0]
        refill = self.store.queue_refill(
            "fake-minute-job", gap["gapId"], {"offset": 1}
        )
        worker = MiningWorker(
            self.config,
            worker_id="production-policy-worker",
            poll_seconds=0.01,
            random_source=random.Random(0),
            store=self.store,
        )

        self.assertEqual(worker.run(once=True), 0)
        job = self.store.get_job("fake-minute-job")
        self.assertEqual(job["status"], "blocked")
        self.assertEqual(job["attemptCount"], before["attemptCount"])
        self.assertEqual(
            (job["pageCount"], job["currentRecords"]),
            (before["pageCount"], before["currentRecords"]),
        )
        self.assertIn("not enabled for worker admission", job["blockedReason"])
        with self.store.transaction() as connection:
            retained_refill = connection.execute(
                "SELECT status FROM refill_tasks WHERE refill_id=?",
                (refill["refillId"],),
            ).fetchone()
        self.assertEqual(retained_refill["status"], "failed")
        event = next(
            item for item in self.store.events() if item["jobId"] == "fake-minute-job"
        )
        self.assertEqual(event["type"], "job.blocked")

    def test_worker_skips_hidden_job_and_claims_next_enabled_provider(self):
        self.create_fake(job_id="hidden-provider-job")
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({"symbol": "BTCUSDT", "interval": "1m"})
        self.store.create_job(
            job_id="enabled-provider-job",
            name="Enabled provider",
            provider=provider.provider_id,
            provider_config=config,
            initial_cursor=provider.initial_cursor(config),
            schedule_seconds=3600,
            overlap_records=0,
            continuity_step=60_000,
        )

        claimed = self.store.claim_next(
            "policy-worker", allowed_providers=enabled_provider_ids()
        )

        self.assertEqual(claimed["jobId"], "enabled-provider-job")
        self.assertEqual(self.store.get_job("hidden-provider-job")["status"], "blocked")
        self.assertEqual(self.store.get_job("hidden-provider-job")["attemptCount"], 0)

    def test_missing_job_subresources_and_actions_return_not_found(self):
        api = MiningApi(self.config)
        missing_job = "missing-mining-job"
        for resource in ("", "/records", "/gaps", "/manifest"):
            with self.subTest(method="GET", resource=resource):
                status, result = api.handle_get(
                    f"/api/mining/jobs/{missing_job}{resource}", {}
                )
                self.assertEqual(status, 404)
                self.assertIn("does not exist", result["error"])
        for action in ("pause", "resume", "run-now"):
            with self.subTest(method="POST", action=action):
                status, result = api.handle_post(
                    f"/api/mining/jobs/{missing_job}/{action}", {}
                )
                self.assertEqual(status, 404)
                self.assertFalse(result["accepted"])
                self.assertIn("does not exist", result["error"])
        status, result = api.handle_post(
            f"/api/mining/jobs/{missing_job}/gaps/{'0' * 24}/refill", {}
        )
        self.assertEqual(status, 404)
        self.assertFalse(result["accepted"])
        self.assertIn("does not exist", result["error"])

    def test_missing_gap_refill_returns_not_found(self):
        self.create_fake()
        api = MiningApi({**self.config, "miningExposeTestProvider": True})
        status, result = api.handle_post(
            f"/api/mining/jobs/fake-minute-job/gaps/{'0' * 24}/refill", {}
        )
        self.assertEqual(status, 404)
        self.assertFalse(result["accepted"])
        self.assertIn("gap does not exist", result["error"])

    def test_explicit_non_object_provider_config_is_not_defaulted(self):
        api = MiningApi({**self.config, "miningExposeTestProvider": True})
        base = {
            "jobId": "strict-provider-config",
            "name": "Strict provider config",
            "provider": "deterministic-fake",
        }
        for value in (None, False, []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "providerConfig.*JSON object"):
                    api.handle_post(
                        "/api/mining/jobs", {**base, "providerConfig": value}
                    )
        self.assertEqual(self.store.list_jobs(), [])

    def test_fake_provider_config_rejects_adjacent_implicit_coercion(self):
        provider = get_provider("deterministic-fake")
        base = {"records": [[0]], "identityPointer": "/0", "eventTimePointer": "/0"}
        for field, value in (
            ("pageSize", False),
            ("pageSize", "2"),
            ("pageSize", 0),
            ("identityPointer", 0),
            ("eventTimePointer", None),
            ("finalPointer", False),
            ("retryAfter", "0"),
            ("retryAfter", True),
            ("failAttempts", False),
            ("failAttempts", [True]),
            ("invalidPageAttempts", None),
            ("failureKind", False),
            ("failureKind", "unknown"),
            ("attemptRecords", False),
            ("attemptRecords", []),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "Fake"):
                    provider.validate_config({**base, field: value})

    def test_get_api_rejects_unknown_repeated_and_out_of_range_query(self):
        api = MiningApi(self.config)
        with self.assertRaisesRegex(ValueError, "Unknown mining query"):
            api.handle_get("/api/mining/jobs", {"cursor": ["x"]})
        with self.assertRaisesRegex(ValueError, "exactly once"):
            api.handle_get("/api/mining/jobs", {"limit": ["10", "20"]})
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            api.handle_get("/api/mining/jobs", {"limit": ["501"]})
        with self.assertRaisesRegex(ValueError, "Invalid mining jobId"):
            api.handle_get("/api/mining/jobs/../records", {})
        with self.assertRaisesRegex(ValueError, "Unknown mining query"):
            api.handle_post("/api/mining/jobs", {}, {"unexpected": ["1"]})

    def test_limit_query_requires_ascii_decimal_on_every_route(self):
        api = MiningApi(self.config)
        disabled = DisabledMiningApi()
        routes = (
            (api, "/api/mining/jobs"),
            (api, "/api/mining/events"),
            (api, "/api/mining/jobs/missing-mining-job/records"),
            (disabled, "/api/mining/jobs"),
        )
        for target_api, path in routes:
            for value in ("+1", " 1", "1 ", "\u0661", ""):
                with self.subTest(api=type(target_api).__name__, path=path, value=value):
                    with self.assertRaisesRegex(ValueError, "ASCII decimal"):
                        target_api.handle_get(path, {"limit": [value]})

    def test_mining_root_is_explicit_and_cannot_overlap_engine_roots(self):
        root = Path(self.temporary.name)
        with self.assertRaisesRegex(ValueError, "explicit independent"):
            MiningStore({"controlRoot": str(root / "control")})
        with self.assertRaisesRegex(ValueError, "overlap controlRoot"):
            MiningStore({
                "controlRoot": str(root / "control"),
                "miningRoot": str(root / "control" / "large-data"),
            })
        control = root / "disabled-control"
        disabled = DisabledMiningApi()
        status, health = disabled.handle_get("/api/mining/health", {})
        self.assertEqual((status, health["status"]), (200, "disabled"))
        self.assertFalse(control.exists())

    def test_page_budget_yields_fairly_and_resumes_from_committed_cursor(self):
        self.config["miningMaxPagesPerRun"] = 1
        self.create_fake({
            "records": [[0, "a"], [1, "b"], [2, "c"]],
            "pageSize": 1, "identityPointer": "/0", "eventTimePointer": "/0",
        }, job_id="first-fair-job", continuity_step=1, overlap_records=0)
        self.create_fake({
            "records": [[10, "x"], [11, "y"]],
            "pageSize": 1, "identityPointer": "/0", "eventTimePointer": "/0",
        }, job_id="second-fair-job", continuity_step=1, overlap_records=0)
        self.run_once()
        first = self.store.get_job("first-fair-job")
        self.assertEqual((first["status"], first["cursor"]), ("queued", {"offset": 1}))
        self.run_once()
        second = self.store.get_job("second-fair-job")
        self.assertEqual((second["status"], second["cursor"]), ("queued", {"offset": 1}))
        self.assertEqual(self.store.get_job("first-fair-job")["cursor"], {"offset": 1})
        for _ in range(10):
            self.run_once()
            if all(self.store.get_job(job_id)["status"] == "succeeded" for job_id in ("first-fair-job", "second-fair-job")):
                break
        self.assertEqual(self.store.get_job("first-fair-job")["currentRecords"], 3)
        self.assertEqual(self.store.get_job("second-fair-job")["currentRecords"], 2)

    def test_provider_rate_slot_is_persistent_and_binance_has_safety_floor(self):
        self.assertEqual(self.store.reserve_rate_slot("provider:host", 0.2), 0)
        self.assertGreater(self.store.reserve_rate_slot("provider:host", 0.2), 0.15)
        provider = get_provider("binance-spot-klines")
        config = provider.validate_config({
            "symbol": "BTCUSDT", "interval": "1m", "requestIntervalSeconds": 0,
        })
        self.assertEqual(provider.minimum_request_interval(config), 0.2)

    def test_numeric_inputs_reject_nan_infinity_and_absurd_values(self):
        api = MiningApi({**self.config, "miningExposeTestProvider": True})
        base = {
            "jobId": "strict-number-job", "name": "Strict", "provider": "deterministic-fake",
            "providerConfig": {"records": [[0]], "identityPointer": "/0", "eventTimePointer": "/0"},
        }
        with self.assertRaisesRegex(ValueError, "continuityStep must be finite"):
            api.handle_post("/api/mining/jobs", {**base, "continuityStep": float("nan")})
        with self.assertRaisesRegex(ValueError, "scheduleSeconds must be between"):
            api.handle_post("/api/mining/jobs", {**base, "scheduleSeconds": 10**30})
        for field, value in (
            ("scheduleSeconds", 60.0),
            ("scheduleSeconds", True),
            ("overlapRecords", "2"),
            ("continuityStep", True),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "must be"):
                    api.handle_post("/api/mining/jobs", {**base, field: value})
        provider = get_provider("binance-spot-klines")
        with self.assertRaisesRegex(ValueError, "requestIntervalSeconds"):
            provider.validate_config({
                "symbol": "BTCUSDT", "interval": "1m", "requestIntervalSeconds": float("inf")
            })
        with self.assertRaisesRegex(ValueError, "request interval must be finite"):
            self.store.reserve_rate_slot("bad", float("nan"))

    def test_job_identity_fields_reject_implicit_string_coercion(self):
        api = MiningApi({**self.config, "miningExposeTestProvider": True})
        base = {
            "jobId": "strict-identity-job",
            "name": "Strict identity",
            "provider": "deterministic-fake",
            "providerConfig": {
                "records": [[0]],
                "identityPointer": "/0",
                "eventTimePointer": "/0",
            },
        }
        for field, value in (
            ("jobId", 123),
            ("name", 123),
            ("provider", 123),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    api.handle_post("/api/mining/jobs", {**base, field: value})

    def test_health_hot_path_uses_cached_integrity_and_observation_index(self):
        health = self.store.health()
        self.assertEqual((health["database"], health["integrity"]), ("connected", "not_checked"))
        self.assertNotIn("miningRoot", health)
        connection = self.store._connect()
        try:
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(record_observations)")}
        finally:
            connection.close()
        self.assertIn("observations_job_idx", indexes)
        self.store.run_integrity_check()
        full = self.store.health()
        self.assertEqual(full["integrity"], "ok")
        self.assertIsNotNone(full["integrityCheckedAt"])

    def test_store_rejects_an_unknown_schema_version(self):
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE schema_meta SET value='999' WHERE key='schemaVersion'"
            )

        with self.assertRaisesRegex(ValueError, r"(?i)schema.*(version|metadata)"):
            MiningStore(self.config)

    def test_store_rejects_version_only_authority_without_rewriting_it(self):
        connection = sqlite3.connect(self.store.db_path)
        try:
            journal_mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            connection.execute(
                "DELETE FROM schema_meta WHERE key='schemaFingerprint'"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(journal_mode, "delete")

        with self.assertRaisesRegex(ValueError, r"(?i)schema authority.*exactly"):
            MiningStore(self.config)

        connection = sqlite3.connect(self.store.db_path)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM schema_meta"))
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(metadata, {"schemaVersion": "1"})
        self.assertEqual(journal_mode, "delete")

    def test_store_rejects_extra_schema_authority(self):
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES('unexpected','value')"
            )

        with self.assertRaisesRegex(ValueError, r"(?i)schema authority.*exactly"):
            MiningStore(self.config)

    def test_store_rejects_an_incompatible_schema_structure(self):
        with self.store.transaction() as connection:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN incompatible_field TEXT"
            )

        with self.assertRaisesRegex(
            ValueError,
            r"(?i)(schema|structure|fingerprint)",
        ):
            MiningStore(self.config)

    def test_commit_page_renews_the_lease_for_the_requested_duration(self):
        self.create_fake({
            "records": [[0, "first"], [1, "second"]],
            "pageSize": 1,
            "identityPointer": "/0",
            "eventTimePointer": "/0",
        }, overlap_records=0, continuity_step=1)
        owner = "lease-duration-worker"
        lease_seconds = 123.0
        claimed = self.store.claim_next(
            owner,
            lease_seconds=lease_seconds,
            allowed_providers=enabled_provider_ids(include_test=True),
        )
        provider = get_provider(claimed["provider"])
        config = claimed["providerConfig"]
        cursor = claimed["cursor"]
        self.store.begin_active(
            claimed["jobId"],
            owner,
            lane="main",
            cursor=cursor,
            refill_id=None,
            lease_seconds=lease_seconds,
        )
        self.store.transition(
            claimed["jobId"], owner, "fetching", lease_seconds=lease_seconds
        )
        page = provider().fetch_page(
            cursor,
            {**config, "_runtimeAttempt": claimed["attemptCount"]},
            None,
        )
        next_cursor = provider.next_cursor(cursor, page, config)
        self.store.transition(
            claimed["jobId"], owner, "committing", lease_seconds=lease_seconds
        )

        started_at = time.time()
        result = self.store.commit_page(
            job_id=claimed["jobId"],
            owner=owner,
            lane="main",
            refill_id=None,
            request_cursor=cursor,
            next_cursor=next_cursor,
            raw=page.raw,
            response_status=page.status_code,
            response_headers=page.headers,
            source=page.source,
            entries=record_entries(provider, page.records, config),
            continue_fetch=True,
            lease_seconds=lease_seconds,
        )
        finished_at = time.time()

        renewed = self.store.get_job(claimed["jobId"])
        self.assertEqual(result["status"], "leased")
        self.assertGreaterEqual(
            renewed["leaseExpiresAt"], started_at + lease_seconds
        )
        self.assertLessEqual(
            renewed["leaseExpiresAt"], finished_at + lease_seconds
        )

    def test_public_job_view_does_not_return_provider_config(self):
        self.create_fake()
        public = self.store.get_job("fake-minute-job")
        internal = self.store.get_job("fake-minute-job", internal=True)
        self.assertNotIn("providerConfig", public)
        self.assertIn("providerConfig", internal)

    def test_export_rejects_a_missing_job_without_creating_a_file(self):
        target = Path(self.temporary.name) / "missing-job.jsonl"
        with self.assertRaises(ValueError):
            self.store.export_records("missing-job", target)
        self.assertFalse(target.exists())

    def test_manifest_is_rebuilt_from_db_after_post_commit_checkpoint_crash(self):
        self.create_fake({
            "records": [[0, "native"]], "pageSize": 1,
            "identityPointer": "/0", "eventTimePointer": "/0",
        }, overlap_records=0)
        owner = "manifest-crash-worker"
        claimed = self.store.claim_next(
            owner, allowed_providers=enabled_provider_ids(include_test=True)
        )
        provider = get_provider(claimed["provider"])
        config = claimed["providerConfig"]
        cursor = claimed["cursor"]
        self.store.begin_active(claimed["jobId"], owner, lane="main", cursor=cursor, refill_id=None)
        self.store.transition(claimed["jobId"], owner, "fetching")
        page = provider().fetch_page(cursor, {**config, "_runtimeAttempt": 1}, None)
        next_cursor = provider.next_cursor(cursor, page, config)
        self.store.transition(claimed["jobId"], owner, "committing")
        with self.assertRaisesRegex(RuntimeError, "after SQLite commit"):
            self.store.commit_page(
                job_id=claimed["jobId"], owner=owner, lane="main", refill_id=None,
                request_cursor=cursor, next_cursor=next_cursor, raw=page.raw,
                response_status=200, response_headers={}, source=page.source,
                entries=record_entries(provider, page.records, config), fault_stage="after_db",
            )
        manifest = self.store.manifest(claimed["jobId"])
        self.assertEqual((manifest["pageCount"], len(manifest["pages"])), (1, 1))
        self.assertEqual(manifest["pages"][0]["pageId"], self.store.events(1)[0]["payload"]["pageId"])

    def test_supervisor_retries_standby_after_single_writer_exit(self):
        class ExitedProcess:
            exitcode = 73
            pid = 123
            def join(self, timeout=None): pass
            def is_alive(self): return False

        supervisor = MiningSupervisor({"miningStandbyRetrySeconds": 0.01})
        supervisor._process = ExitedProcess()
        spawned = []
        def spawn_once():
            spawned.append(True)
            supervisor._stopping = True
        supervisor._spawn_locked = spawn_once
        supervisor._monitor()
        self.assertEqual((len(spawned), supervisor._restart_count), (1, 1))

    def test_supervisor_shutdown_rejects_a_worker_that_remains_alive(self):
        class StuckProcess:
            pid = 123

            def __init__(self):
                self.terminate_calls = 0

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return True

            def terminate(self):
                self.terminate_calls += 1

        supervisor = MiningSupervisor({})
        process = StuckProcess()
        supervisor._process = process

        with self.assertRaises(RuntimeError):
            supervisor.shutdown(timeout=0.001)
        self.assertEqual(process.terminate_calls, 1)

    def test_supervisor_shutdown_rejects_a_monitor_that_remains_alive(self):
        class StoppedProcess:
            pid = 123

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

        class StuckThread:
            def join(self, timeout=None):
                return None

            def is_alive(self):
                return True

        supervisor = MiningSupervisor({})
        supervisor._process = StoppedProcess()
        supervisor._thread = StuckThread()

        with self.assertRaises(RuntimeError):
            supervisor.shutdown(timeout=0.001)


if __name__ == "__main__":
    unittest.main()
