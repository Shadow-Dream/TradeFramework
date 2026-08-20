#!/usr/bin/env python3

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.contracts import strict_json
from engine.runtime import result_stream


class ResultArchiveStreamingTests(unittest.TestCase):
    def test_disk_backed_identity_index_is_exact_and_cleans_up(self):
        index = result_stream.UniqueTextIndex(prefix="trade-test-identities-")
        directory = index.path.parent
        self.assertTrue(index.claim("cycle-1"))
        self.assertTrue(index.claim("cycle-2"))
        self.assertFalse(index.claim("cycle-1"))
        index.close()
        index.close()
        self.assertFalse(directory.exists())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            index.claim("cycle-3")

    def write_raw(self, root, text):
        path = Path(root) / "result.json"
        path.write_text(text, encoding="utf-8")
        raw = path.read_bytes()
        return path, "sha256:" + hashlib.sha256(raw).hexdigest(), len(raw)

    def minimal_tail(self):
        return (
            ',"schemaVersion":8,"dataKeys":{},"metrics":{"cycleCount":1},'
            '"executionChain":{},"sampleFrameContract":{}}'
        )

    def write_framed(self, root, cycles):
        metadata = {
            "schemaVersion": 8,
            "dataKeys": {},
            "metrics": {"cycleCount": len(cycles)},
            "executionChain": {},
            "sampleFrameContract": {},
        }
        body = ",\n".join(
            strict_json.dumps(
                cycle, sort_keys=True, separators=(",", ":")
            )
            for cycle in cycles
        )
        suffix = "".join(
            "," + strict_json.dumps(key) + ":" + strict_json.dumps(
                value, sort_keys=True, separators=(",", ":")
            )
            for key, value in metadata.items()
        )
        text = '{"cycles":[\n' + body + ("\n]" if cycles else "]") + suffix + "}"
        path, digest, size = self.write_raw(root, text)
        return path, digest, size, metadata

    def test_framed_ranges_cover_every_byte_and_merge_global_identities(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": f"cycle-{index}",
                "decisionTime": f"2026-01-0{index + 1}T00:00:00Z",
                "data": {"text": "数据\\ninside"},
            }
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ):
            path, digest, size, metadata = self.write_framed(root, cycles)
            plan = result_stream.plan_framed_cycle_ranges(
                path, expected_size=size
            )
            self.assertGreater(len(plan["ranges"]), 1)
            self.assertEqual(plan["ranges"][0]["start"], plan["cycleStart"])
            self.assertEqual(plan["ranges"][-1]["end"], plan["metadataStart"])
            self.assertTrue(all(
                left["end"] == right["start"]
                for left, right in zip(plan["ranges"], plan["ranges"][1:])
            ))
            decoded = []
            for byte_range in plan["ranges"]:
                decoded.extend(result_stream.iter_framed_cycle_values(
                    path,
                    byte_range["start"],
                    byte_range["end"],
                    final_range=byte_range["final"],
                ))
            self.assertEqual(decoded, cycles)
            self.assertEqual(
                result_stream.read_framed_result_metadata(
                    path,
                    metadata_start=plan["metadataStart"],
                    expected_size=size,
                ),
                metadata,
            )
            self.assertEqual(
                result_stream.hash_result_archive(path, expected_size=size),
                digest,
            )

            first = result_stream.ResultCycleIdentityLedger(
                Path(root) / "first.sqlite3"
            )
            first.select_cycle(0)
            self.assertTrue(first.claim("shared"))
            first.close()
            second = result_stream.ResultCycleIdentityLedger(
                Path(root) / "second.sqlite3"
            )
            second.select_cycle(0)
            self.assertTrue(second.claim("shared"))
            second.close()
            duplicate = result_stream.merge_cycle_identity_ledgers(
                (
                    {"path": first.path, "baseIndex": 0},
                    {"path": second.path, "baseIndex": 3},
                ),
                Path(root) / "merged.sqlite3",
            )
            self.assertEqual(duplicate, 3)

    def test_framed_range_rejects_a_separator_that_is_not_completely_consumed(self):
        cycle = {
            "schemaVersion": 3,
            "cycleId": "cycle-1",
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
        }
        with tempfile.TemporaryDirectory() as root:
            path, _digest, size, _metadata = self.write_framed(
                root, [cycle, {**cycle, "cycleId": "cycle-2"}]
            )
            raw = path.read_bytes().replace(b"},\n{", b"}\n{", 1)
            path.write_bytes(raw)
            plan = result_stream.plan_framed_cycle_ranges(
                path, expected_size=size - 1
            )
            with self.assertRaisesRegex(ValueError, "separator framing"):
                list(result_stream.iter_framed_cycle_values(
                    path,
                    plan["ranges"][0]["start"],
                    plan["ranges"][0]["end"],
                    final_range=plan["ranges"][0]["final"],
                ))

    def test_streaming_decoder_rejects_duplicate_keys_and_nonfinite_numbers(self):
        invalid_cycles = (
            '{"cycles":[{"schemaVersion":3,"cycleId":"a","cycleId":"b",'
            '"decisionTime":"t","data":{}}]'
            + self.minimal_tail()
        )
        invalid_nested_metadata = (
            '{"cycles":[{"schemaVersion":3,"cycleId":"a",'
            '"decisionTime":"t","data":{}}],'
            '"schemaVersion":8,"dataKeys":{},'
            '"metrics":{"cycleCount":1,"cycleCount":2},'
            '"executionChain":{},"sampleFrameContract":{}}'
        )
        invalid_number = (
            '{"cycles":[{"schemaVersion":3,"cycleId":"a",'
            '"decisionTime":"t","data":{"x":NaN}}]'
            + self.minimal_tail()
        )
        for text in (invalid_cycles, invalid_nested_metadata, invalid_number):
            with self.subTest(text=text[:40]), tempfile.TemporaryDirectory() as root:
                path, digest, size = self.write_raw(root, text)
                with self.assertRaises(ValueError):
                    with result_stream.ResultArchiveReader(
                        path, expected_digest=digest, expected_size=size
                    ) as reader:
                        list(reader.cycles())

    def test_projection_keeps_cycle_identity_and_only_requested_data(self):
        payload = {
            "cycles": [{
                "schemaVersion": 3,
                "cycleId": "cycle-1",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {"x": 1, "large": {str(i): i for i in range(100)}},
            }],
            "schemaVersion": 8,
            "dataKeys": {},
            "metrics": {"cycleCount": 1},
            "executionChain": {},
            "sampleFrameContract": {},
        }
        with tempfile.TemporaryDirectory() as root:
            text = strict_json.dumps(payload, separators=(",", ":"))
            path, digest, size = self.write_raw(root, text)
            output = Path(root) / "slice.json"
            seen = {}

            def validate_metadata(metadata, **summary):
                seen.update(summary)
                self.assertEqual(metadata["metrics"], {"cycleCount": 1})

            result_stream.write_projection(
                path,
                output,
                paths=["cycles.data.x"],
                data_keys={},
                expected_digest=digest,
                expected_size=size,
                prepare_cycle=lambda _index, cycle: cycle,
                finalize_cycles=lambda: None,
                validate_metadata=validate_metadata,
            )
            projected = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(projected["cycles"], [{
                "schemaVersion": 3,
                "cycleId": "cycle-1",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {"x": 1},
            }])
            self.assertEqual(seen["cycle_count"], 1)

    def test_empty_projection_remains_self_describing(self):
        payload = {
            "cycles": [],
            "schemaVersion": 8,
            "dataKeys": {"x": {"schema": {"type": "number"}}},
            "metrics": {"cycleCount": 0},
            "executionChain": {},
            "sampleFrameContract": {},
        }
        with tempfile.TemporaryDirectory() as root:
            text = strict_json.dumps(payload, separators=(",", ":"))
            path, digest, size = self.write_raw(root, text)
            output = Path(root) / "slice.json"
            result_stream.write_projection(
                path,
                output,
                paths=[],
                data_keys=payload["dataKeys"],
                expected_digest=digest,
                expected_size=size,
                prepare_cycle=lambda _index, cycle: cycle,
                finalize_cycles=lambda: None,
                validate_metadata=lambda _metadata, **_summary: None,
            )
            self.assertEqual(
                strict_json.loads(output.read_text(encoding="utf-8")),
                {"dataKeys": payload["dataKeys"]},
            )

    def test_projection_cleanup_preserves_the_archive_failure_and_attempts_every_path(self):
        primary = RuntimeError("archive failed first")
        cleanup = OSError("projection cleanup failed later")
        temporary = mock.Mock()
        destination = mock.Mock()
        destination.name = "projection.json"
        destination.with_name.return_value = temporary
        temporary.unlink.side_effect = (None, cleanup)
        destination.unlink.side_effect = cleanup
        with (
            mock.patch.object(result_stream, "Path", return_value=destination),
            mock.patch.object(
                result_stream,
                "ResultArchiveReader",
                side_effect=primary,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "archive failed first") as raised:
                result_stream.write_projection(
                    "source.json",
                    "projection.json",
                    paths=[],
                    data_keys={},
                    expected_digest="sha256:" + ("0" * 64),
                    expected_size=1,
                    prepare_cycle=lambda _index, cycle: cycle,
                    finalize_cycles=lambda: None,
                    validate_metadata=lambda _metadata, **_summary: None,
                )
        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__context__, cleanup)
        self.assertEqual(temporary.unlink.call_count, 2)
        destination.unlink.assert_called_once_with(missing_ok=True)

    def test_reader_and_output_cleanup_cannot_replace_the_cycle_failure(self):
        primary = RuntimeError("cycle read failed first")
        output_cleanup = OSError("output close failed later")
        reader_cleanup = OSError("reader close failed later")
        destination = mock.Mock(name="destination")
        destination.name = "projection.json"
        temporary = mock.Mock(name="temporary")
        destination.with_name.return_value = temporary
        output = mock.Mock(name="output")
        output.close.side_effect = output_cleanup
        temporary.open.return_value = output

        reader = mock.Mock(name="reader")
        reader.__enter__ = mock.Mock(return_value=reader)
        reader.__exit__ = mock.Mock(side_effect=reader_cleanup)
        reader.cycles.side_effect = primary

        with (
            mock.patch.object(result_stream, "Path", return_value=destination),
            mock.patch.object(
                result_stream,
                "ResultArchiveReader",
                return_value=reader,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "cycle read failed first") as raised:
                result_stream.write_projection(
                    "source.json",
                    "projection.json",
                    paths=["cycles"],
                    data_keys={},
                    expected_digest="sha256:" + ("0" * 64),
                    expected_size=1,
                    prepare_cycle=lambda _index, cycle: cycle,
                    finalize_cycles=lambda: None,
                    validate_metadata=lambda _metadata, **_summary: None,
                )

        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__context__, output_cleanup)
        output.close.assert_called_once()
        reader.__exit__.assert_called_once_with(None, None, None)


if __name__ == "__main__":
    unittest.main()
