#!/usr/bin/env python3
"""Prepared Backtest submission authority and lifecycle regressions."""

import copy
import threading
import unittest
from unittest import mock

from engine.contracts import backtest as backtest_contracts
from engine.service import backtest_submissions
from engine.service.backtest_submissions import PreparedBacktestSubmissionStore


def request():
    return {
        "pipeline": {"pipelineId": "pipeline", "version": "1"},
        "datasetId": "dataset",
        "datasetVersionId": "dataset@sha256:version",
        "sampler": {
            "samplerId": "sampler",
            "version": "1",
            "parameters": {"mapping": {"value": "value"}},
        },
        "environment": {"environmentId": "environment", "version": "1"},
        "analysis": {"analysisId": "analysis", "version": "1"},
    }


def frozen_request(raw):
    frozen = copy.deepcopy(raw)
    snapshot = {
        "executionInputs": backtest_contracts.backtest_execution_inputs(raw),
        "proof": {"complete": True},
    }
    snapshot["snapshotHash"] = backtest_contracts.backtest_evidence_digest(
        snapshot
    )
    frozen["executionSnapshot"] = snapshot
    return frozen


class PreparedBacktestSubmissionStoreTests(unittest.TestCase):
    def _frozen_for(self, raw):
        frozen = frozen_request(raw)
        frozen["executionSnapshot"]["compositionArtifact"] = {
            "pipelinePlan": {"topology": []},
            "environmentPlan": {"topology": []},
            "analysisPlan": {"topology": []},
            "resultContracts": {},
        }
        frozen["executionSnapshot"]["snapshotHash"] = (
            backtest_contracts.backtest_evidence_digest(
                {
                    key: value
                    for key, value in frozen["executionSnapshot"].items()
                    if key != "snapshotHash"
                }
            )
        )
        return frozen

    def issue(self, store, raw=None, *, session="session-a"):
        raw = request() if raw is None else raw
        frozen = self._frozen_for(raw)
        with mock.patch(
            "engine.service.backtests.freeze_backtest_request",
            return_value=frozen,
        ):
            prepared = backtest_submissions.prepare_backtest_submission(
                {},
                raw,
                store,
                session_identity=session,
            )
        return raw, prepared["preparedSubmissionToken"]

    def test_consumes_exact_request_once_without_exposing_frozen_material(self):
        store = PreparedBacktestSubmissionStore()
        raw, token = self.issue(store)

        consumed = store.consume(token, raw, session_identity="session-a")

        expected = frozen_request(raw)
        expected["executionSnapshot"]["compositionArtifact"] = {
            "pipelinePlan": {"topology": []},
            "environmentPlan": {"topology": []},
            "analysisPlan": {"topology": []},
            "resultContracts": {},
        }
        expected["executionSnapshot"]["snapshotHash"] = (
            backtest_contracts.backtest_evidence_digest(
                {
                    key: value
                    for key, value in expected["executionSnapshot"].items()
                    if key != "snapshotHash"
                }
            )
        )
        self.assertEqual(consumed, expected)
        _raw, another = self.issue(store, raw)
        self.assertNotEqual(another, token)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            store.consume(token, raw, session_identity="session-a")

    def test_wrong_session_and_tampered_request_fail_closed_and_consume_token(self):
        store = PreparedBacktestSubmissionStore()
        raw, token = self.issue(store)
        with self.assertRaisesRegex(PermissionError, "another session"):
            store.consume(token, raw, session_identity="session-b")
        with self.assertRaisesRegex(ValueError, "already consumed"):
            store.consume(token, raw, session_identity="session-a")

        raw, token = self.issue(store)
        tampered = copy.deepcopy(raw)
        tampered["sampler"]["parameters"]["mapping"]["value"] = "other"
        with self.assertRaisesRegex(ValueError, "exact request"):
            store.consume(token, tampered, session_identity="session-a")
        with self.assertRaisesRegex(ValueError, "already consumed"):
            store.consume(token, raw, session_identity="session-a")

    def test_same_session_replaces_its_previous_outstanding_authority(self):
        store = PreparedBacktestSubmissionStore(max_entries=1)
        first_raw, first = self.issue(store)
        second_raw = request()
        second_raw["limit"] = 1

        _second_raw, second = self.issue(store, second_raw)

        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "unknown, expired"):
            store.consume(first, first_raw, session_identity="session-a")
        store.consume(second, second_raw, session_identity="session-a")

    def test_reuses_cached_build_but_issues_a_fresh_one_shot_token(self):
        store = PreparedBacktestSubmissionStore(
            lifetime_seconds=5,
            build_cache_lifetime_seconds=30,
        )
        raw = request()
        frozen = frozen_request(raw)
        frozen["executionSnapshot"]["compositionArtifact"] = {
            "pipelinePlan": {"topology": []},
            "environmentPlan": {"topology": []},
            "analysisPlan": {"topology": []},
            "resultContracts": {},
        }
        frozen["executionSnapshot"]["snapshotHash"] = (
            backtest_contracts.backtest_evidence_digest(
                {
                    key: value
                    for key, value in frozen["executionSnapshot"].items()
                    if key != "snapshotHash"
                }
            )
        )
        with mock.patch(
            "engine.service.backtests.freeze_backtest_request",
            return_value=frozen,
        ) as freeze:
            first = backtest_submissions.prepare_backtest_submission(
                {}, raw, store, session_identity="session-a"
            )
            store.consume(
                first["preparedSubmissionToken"],
                raw,
                session_identity="session-a",
            )
            second = backtest_submissions.prepare_backtest_submission(
                {}, raw, store, session_identity="session-a"
            )

        freeze.assert_called_once_with({}, raw)
        self.assertFalse(first["cacheHit"])
        self.assertTrue(second["cacheHit"])
        self.assertEqual(first["buildCacheExpiresInSeconds"], 30)
        self.assertGreater(second["buildCacheExpiresInSeconds"], 0)
        self.assertNotEqual(
            first["preparedSubmissionToken"],
            second["preparedSubmissionToken"],
        )
        store.consume(
            second["preparedSubmissionToken"],
            raw,
            session_identity="session-a",
        )

    def test_expired_build_cache_requires_a_new_authoritative_freeze(self):
        now = [100.0]
        store = PreparedBacktestSubmissionStore(
            build_cache_lifetime_seconds=10,
            clock=lambda: now[0],
        )
        raw = request()
        with mock.patch(
            "engine.service.backtests.freeze_backtest_request",
            side_effect=lambda _config, incoming: self._frozen_for(incoming),
        ) as freeze:
            first = backtest_submissions.prepare_backtest_submission(
                {}, raw, store, session_identity="session-a"
            )
            store.consume(
                first["preparedSubmissionToken"],
                raw,
                session_identity="session-a",
            )
            now[0] = 111.0
            second = backtest_submissions.prepare_backtest_submission(
                {}, raw, store, session_identity="session-a"
            )

        self.assertEqual(freeze.call_count, 2)
        self.assertFalse(first["cacheHit"])
        self.assertFalse(second["cacheHit"])

    def test_full_capacity_never_evicts_another_session_authority(self):
        store = PreparedBacktestSubmissionStore(max_entries=1)
        first_raw, first = self.issue(store, session="session-a")

        with self.assertRaisesRegex(RuntimeError, "capacity is full"):
            self.issue(store, session="session-b")

        store.consume(first, first_raw, session_identity="session-a")

    def test_expiry_frees_capacity_and_restart_fails_closed(self):
        now = [100.0]
        store = PreparedBacktestSubmissionStore(
            max_entries=1,
            lifetime_seconds=5,
            clock=lambda: now[0],
        )
        first_raw, first = self.issue(store)
        now[0] = 106.0
        second_raw = request()
        second_raw["limit"] = 1
        _second_raw, second = self.issue(
            store,
            second_raw,
            session="session-b",
        )
        with self.assertRaisesRegex(ValueError, "unknown, expired"):
            store.consume(first, first_raw, session_identity="session-a")
        now[0] = 112.0
        with self.assertRaisesRegex(ValueError, "unknown, expired"):
            store.consume(second, second_raw, session_identity="session-b")

        restarted = PreparedBacktestSubmissionStore()
        with self.assertRaisesRegex(ValueError, "unknown, expired"):
            restarted.consume(second, second_raw, session_identity="session-b")

    def test_store_has_no_public_mint_and_prepare_uses_authoritative_freeze(self):
        store = PreparedBacktestSubmissionStore()
        self.assertFalse(hasattr(store, "issue"))
        raw = request()
        frozen = frozen_request(raw)
        frozen["executionSnapshot"]["compositionArtifact"] = {
            "pipelinePlan": {"topology": ["pipeline"]},
            "environmentPlan": {"topology": ["environment"]},
            "analysisPlan": {"topology": ["analysis"]},
            "resultContracts": {"value": {"schema": {"type": "number"}}},
        }
        frozen["executionSnapshot"]["snapshotHash"] = (
            backtest_contracts.backtest_evidence_digest(
                {
                    key: value
                    for key, value in frozen["executionSnapshot"].items()
                    if key != "snapshotHash"
                }
            )
        )
        with mock.patch(
            "engine.service.backtests.freeze_backtest_request",
            return_value=frozen,
        ) as freeze:
            result = backtest_submissions.prepare_backtest_submission(
                {"root": "config"},
                raw,
                store,
                session_identity="session-a",
            )
        freeze.assert_called_once_with({"root": "config"}, raw)
        self.assertEqual(result["pipelineTopology"], ["pipeline"])
        self.assertNotIn("executionSnapshot", result)

    def test_prepare_rejects_invalid_authoritative_snapshot_before_issuance(self):
        store = PreparedBacktestSubmissionStore()
        raw = request()
        forged = frozen_request(raw)
        forged["executionSnapshot"]["snapshotHash"] = "sha256:" + "0" * 64
        with mock.patch(
            "engine.service.backtests.freeze_backtest_request",
            return_value=forged,
        ), self.assertRaisesRegex(ValueError, "invalid snapshot hash"):
            backtest_submissions.prepare_backtest_submission(
                {}, raw, store, session_identity="session-a"
            )

    def test_concurrent_replay_has_exactly_one_winner(self):
        store = PreparedBacktestSubmissionStore()
        raw, token = self.issue(store)
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def consume():
            barrier.wait()
            try:
                store.consume(token, raw, session_identity="session-a")
                outcome = "consumed"
            except ValueError:
                outcome = "rejected"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(outcomes), ["consumed", "rejected"])


if __name__ == "__main__":
    unittest.main()
