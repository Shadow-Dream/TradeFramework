#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from engine.contracts import strict_json
from engine.runtime import result_stream
from engine.worker import result_verifier


class ResultVerifierWorkerTests(unittest.TestCase):
    @staticmethod
    def _cycle(cycle_id, **extra):
        return {
            "schemaVersion": 3,
            "cycleId": cycle_id,
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
            **extra,
        }

    def _write_archive(self, root, cycles):
        encoded = ",\n".join(
            strict_json.dumps(
                cycle, sort_keys=True, separators=(",", ":")
            )
            for cycle in cycles
        )
        metadata = (
            ',"schemaVersion":8,"dataKeys":{},'
            f'"metrics":{{"cycleCount":{len(cycles)}}},'
            '"executionChain":{},"sampleFrameContract":{}}'
        )
        path = Path(root) / "result.json"
        path.write_text(
            '{"cycles":[\n' + encoded + "\n]" + metadata,
            encoding="utf-8",
        )
        plan = result_stream.plan_framed_cycle_ranges(
            path, expected_size=path.stat().st_size, max_shards=1
        )
        return path, plan["ranges"][0]

    def _verify(self, root, cycles):
        root = Path(root)
        result_path, byte_range = self._write_archive(root, cycles)
        spec_path = root / "spec.json"
        outcome_path = root / "outcome.json"
        ledger_path = root / "identities.sqlite3"
        spec_path.write_text(
            strict_json.dumps({
                "schemaVersion": 1,
                "shardIndex": 0,
                "resultPath": str(result_path),
                "rangeStart": byte_range["start"],
                "rangeEnd": byte_range["end"],
                "finalRange": True,
                "dataKeys": {},
                "ledgerPath": str(ledger_path),
                "outcomePath": str(outcome_path),
            }, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        returned = result_verifier.verify_specification(spec_path)
        return returned, strict_json.loads(outcome_path.read_bytes()), ledger_path

    def test_fresh_worker_verifies_every_cycle_and_leaves_disk_identity_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            returned, outcome, ledger_path = self._verify(
                root, [self._cycle("one"), self._cycle("two")]
            )
            self.assertEqual(returned, outcome)
            self.assertEqual(outcome["status"], "verified")
            self.assertEqual(outcome["lineCount"], 2)
            self.assertEqual(outcome["validatedCount"], 2)
            self.assertEqual(outcome["firstCycleId"], "one")
            self.assertEqual(outcome["lastCycleId"], "two")
            self.assertTrue(ledger_path.is_file())

    def test_worker_rejection_retains_symbolic_index_for_parent_ordering(self):
        with tempfile.TemporaryDirectory() as root:
            _returned, outcome, ledger_path = self._verify(
                root, [self._cycle("bad", unexpected=True)]
            )
            self.assertEqual(outcome["status"], "rejected")
            self.assertEqual(outcome["errorLocalIndex"], 0)
            self.assertEqual(outcome["errorType"], "ValueError")
            self.assertIn(
                "__ENGINE_RESULT_CYCLE_INDEX__", outcome["errorMessage"]
            )
            self.assertTrue(ledger_path.is_file())


if __name__ == "__main__":
    unittest.main()
