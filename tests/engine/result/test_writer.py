#!/usr/bin/env python3

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import version as version_archive
from engine.contracts import result as result_contracts
from engine.worker import result_writer


class _PartialBinaryWriter:
    def __init__(self, handle, *, max_write, fail_on_call=None):
        self._handle = handle
        self.max_write = max_write
        self.fail_on_call = fail_on_call
        self.write_calls = 0

    def write(self, value):
        self.write_calls += 1
        if self.write_calls == self.fail_on_call:
            raise OSError("injected Result stream failure")
        return self._handle.write(value[:self.max_write])

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _metadata(*cycle_ids):
    return {
        "schemaVersion": 8,
        "dataKeys": {},
        "metrics": {"cycleCount": len(cycle_ids)},
        "executionChain": {},
        "sampleFrameContract": {
            "frameCount": len(cycle_ids),
            "firstCycleId": cycle_ids[0] if cycle_ids else None,
            "lastCycleId": cycle_ids[-1] if cycle_ids else None,
        },
    }


def _catalog(backtest_id, metadata):
    return {
        "backtestId": backtest_id,
        "pipelineId": "pipeline",
        "datasetId": "dataset",
        "name": "Result writer fixture",
        "runner": "engine.backtest",
        "createdAt": "2026-01-01T00:00:00Z",
        "completedAt": "2026-01-01T00:00:00Z",
        "request": {},
        "metrics": metadata["metrics"],
        "visualization": {},
    }


class BacktestResultWriterTests(unittest.TestCase):
    def test_catalog_rejects_an_unowned_runner_label(self):
        catalog = {
            "backtestId": "bt_01KZSEPRN40Y09QKGEZT48Z7GP",
            "pipelineId": "pipeline",
            "datasetId": "dataset",
            "name": "Result",
            "runner": "local-data-dictionary-backtest",
            "createdAt": "2026-01-01T00:00:00Z",
            "completedAt": "2026-01-01T00:00:00Z",
            "request": {
                "datasetId": "dataset",
                "pipeline": {"pipelineId": "pipeline"},
                "executionSnapshot": {},
            },
            "metrics": {},
            "visualization": {},
        }
        with self.assertRaisesRegex(ValueError, "runner is invalid"):
            result_contracts.require_catalog(
                catalog,
                backtest_id=catalog["backtestId"],
            )

    def test_native_encoder_preserves_json_values_and_strict_fallbacks(self):
        value = {
            "large": 2 ** 80,
            "number": 0.000000123456789,
            "text": "数据",
        }
        encoded = result_writer.BacktestResultWriter._encode_json(value)
        self.assertEqual(json.loads(encoded), value)
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            result_writer.BacktestResultWriter._encode_json({"bad": math.nan})

    def test_encodes_cycle_before_the_source_can_change(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = result_writer.BacktestResultWriter(
                Path(directory) / "result" / "result.json"
            )
            data = {"state": {"rows": [{"value": 7}]}}
            try:
                writer.append(
                    {
                        "schemaVersion": 3,
                        "cycleId": "cycle-1",
                        "decisionTime": "2026-01-01T00:00:00Z",
                        "data": data,
                    }
                )
                encoded = writer._pending[-1]
                data["state"]["rows"][0]["value"] = 99
                data["state"]["rows"].append({"value": 100})
                self.assertEqual(writer._pending[-1], encoded)
                self.assertIn('"rows":[{"value":7}]', encoded)
                self.assertNotIn('"value":99', encoded)
                self.assertNotIn('"value":100', encoded)
            finally:
                writer.discard()

    def test_ordinary_append_preserves_the_strict_json_boundary(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        class StringSubclass(str):
            pass

        class IntegerSubclass(int):
            pass

        class FloatSubclass(float):
            pass

        rejected = (
            float("nan"),
            (1, 2),
            DictSubclass({"hidden": 1}),
            ListSubclass([1]),
        )
        accepted = (
            StringSubclass("text"),
            IntegerSubclass(7),
            FloatSubclass(1.25),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, value in enumerate(rejected):
                with self.subTest(boundary="rejected", value_type=type(value).__name__):
                    writer = result_writer.BacktestResultWriter(
                        Path(directory) / f"rejected-{index}" / "result.json"
                    )
                    try:
                        with self.assertRaises(ValueError):
                            writer.append({
                                "schemaVersion": 3,
                                "cycleId": f"rejected-{index}",
                                "decisionTime": "2026-01-01T00:00:00Z",
                                "data": {"value": value},
                            })
                    finally:
                        writer.discard()

            for index, value in enumerate(accepted):
                with self.subTest(boundary="accepted", value_type=type(value).__name__):
                    writer = result_writer.BacktestResultWriter(
                        Path(directory) / f"accepted-{index}" / "result.json"
                    )
                    try:
                        writer.append({
                            "schemaVersion": 3,
                            "cycleId": f"accepted-{index}",
                            "decisionTime": "2026-01-01T00:00:00Z",
                            "data": {"value": value},
                        })
                        self.assertEqual(
                            json.loads(writer._pending[-1])["data"]["value"],
                            value,
                        )
                    finally:
                        writer.discard()

    def test_batches_cycles_without_changing_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            publish_events = []
            real_publish = version_archive.publish_staging_directory

            def publish(staging, destination, *, managed_root):
                staging = Path(staging)
                self.assertEqual(Path(destination), path.parent)
                self.assertEqual(Path(managed_root), path.parent.parent)
                for candidate in (staging, *staging.rglob("*")):
                    self.assertEqual(
                        candidate.stat().st_mode & 0o222,
                        0,
                        f"Result publication received writable path {candidate}",
                    )
                publish_events.append(staging)
                return real_publish(
                    staging,
                    destination,
                    managed_root=managed_root,
                )

            writer.flush_characters = 1024 * 1024
            cycles = [
                {
                    "schemaVersion": 3,
                    "cycleId": "one",
                    "decisionTime": "2026-01-01T00:00:00Z",
                    "data": {"value": 1.0},
                },
                {
                    "schemaVersion": 3,
                    "cycleId": "two",
                    "decisionTime": "2026-01-02T00:00:00Z",
                    "data": {"value": "数据"},
                },
            ]
            for cycle in cycles:
                writer.append(cycle)
            self.assertEqual(writer.write_seconds, 0.0)
            writer.flush_cycles()
            self.assertGreater(writer.write_seconds, 0.0)
            metadata = {
                "schemaVersion": 8,
                "dataKeys": {},
                "metrics": {"cycleCount": 2},
                "executionChain": {},
                "sampleFrameContract": {
                    "frameCount": 2,
                    "firstCycleId": "one",
                    "lastCycleId": "two",
                },
            }
            with mock.patch.object(
                version_archive,
                "publish_staging_directory",
                side_effect=publish,
            ):
                completion = writer.finish(
                    metadata,
                    {
                        "backtestId": "result",
                        "pipelineId": "pipeline",
                        "datasetId": "dataset",
                        "name": "Result writer fixture",
                        "runner": "engine.backtest",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "completedAt": "2026-01-01T00:00:00Z",
                        "request": {},
                        "metrics": metadata["metrics"],
                        "visualization": {},
                    },
                )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"cycles": cycles, **metadata},
            )
            raw_result = path.read_bytes()
            expected_text = (
                '{"cycles":[\n'
                + ",\n".join(
                    result_writer.BacktestResultWriter._encode_json(cycle)
                    for cycle in cycles
                )
                + "\n]"
                + "".join(
                    ","
                    + result_writer.BacktestResultWriter._encode_json(str(key))
                    + ":"
                    + result_writer.BacktestResultWriter._encode_json(value)
                    for key, value in metadata.items()
                )
                + "}"
            ).encode("utf-8")
            self.assertEqual(raw_result, expected_text)
            self.assertTrue(raw_result.startswith(b'{"cycles":[\n'))
            self.assertIn(b"},\n{", raw_result)
            self.assertIn(b'\n],"schemaVersion":8', raw_result)
            manifest = json.loads(
                (path.parent / "result-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            expected_digest = "sha256:" + hashlib.sha256(raw_result).hexdigest()
            self.assertEqual(manifest["schemaVersion"], 4)
            self.assertEqual(manifest["contentDigest"], expected_digest)
            self.assertEqual(manifest["size"], len(raw_result))
            self.assertEqual(completion["contentDigest"], expected_digest)
            self.assertEqual(completion["resultSize"], len(raw_result))
            self.assertEqual(writer.content_digest, expected_digest)
            self.assertEqual(writer.result_size, len(raw_result))
            self.assertEqual(writer.count, 2)
            self.assertGreater(writer.encoded_characters, 0)
            self.assertGreater(completion["finishSeconds"], 0.0)
            self.assertGreaterEqual(completion["fsyncSeconds"], 0.0)
            self.assertEqual(len(publish_events), 1)
            self.assertTrue(writer.published)

    def test_streamed_digest_does_not_reopen_the_complete_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            writer.append({
                "schemaVersion": 3,
                "cycleId": "one",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {"value": 1},
            })
            metadata = _metadata("one")
            original_open = Path.open

            def reject_result_read(candidate, mode="r", *args, **kwargs):
                if Path(candidate) == writer.staging_path and "r" in mode:
                    raise AssertionError("finished Result was reopened for reading")
                return original_open(candidate, mode, *args, **kwargs)

            with mock.patch.object(Path, "open", new=reject_result_read):
                writer.finish(metadata, _catalog("result", metadata))

            raw_result = path.read_bytes()
            self.assertEqual(
                writer.content_digest,
                "sha256:" + hashlib.sha256(raw_result).hexdigest(),
            )

    def test_finish_encodes_manifest_before_caller_sources_can_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            metadata = _metadata()
            metadata["executionChain"] = {"nested": {"value": "before"}}
            catalog = _catalog("result", metadata)
            catalog["request"] = {"nested": {"value": "before"}}

            writer.finish(metadata, catalog)
            manifest_path = path.parent / "result-manifest.json"
            encoded = manifest_path.read_bytes()

            metadata["executionChain"]["nested"]["value"] = "after"
            catalog["request"]["nested"]["value"] = "after"
            self.assertEqual(manifest_path.read_bytes(), encoded)
            manifest = json.loads(encoded)
            self.assertEqual(
                manifest["resultMetadata"]["executionChain"]["nested"]["value"],
                "before",
            )
            self.assertEqual(
                manifest["catalog"]["request"]["nested"]["value"],
                "before",
            )

    def test_partial_binary_writes_hash_the_exact_accepted_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            partial = _PartialBinaryWriter(writer.handle, max_write=7)
            writer.handle = partial
            writer.flush_characters = 1
            cycles = [
                {
                    "schemaVersion": 3,
                    "cycleId": "one",
                    "decisionTime": "2026-01-01T00:00:00Z",
                    "data": {"text": "数据", "value": 1},
                },
                {
                    "schemaVersion": 3,
                    "cycleId": "two",
                    "decisionTime": "2026-01-02T00:00:00Z",
                    "data": {"text": "result", "value": 2},
                },
            ]
            for cycle in cycles:
                writer.append(cycle)
            metadata = _metadata("one", "two")
            completion = writer.finish(metadata, _catalog("result", metadata))

            raw_result = path.read_bytes()
            expected_digest = "sha256:" + hashlib.sha256(raw_result).hexdigest()
            manifest = json.loads(
                (path.parent / "result-manifest.json").read_text(encoding="utf-8")
            )
            self.assertGreater(partial.write_calls, 2)
            self.assertEqual(json.loads(raw_result), {"cycles": cycles, **metadata})
            self.assertEqual(writer._result_byte_count, len(raw_result))
            self.assertEqual(completion["contentDigest"], expected_digest)
            self.assertEqual(manifest["contentDigest"], expected_digest)
            self.assertEqual(manifest["size"], len(raw_result))

    def test_partial_write_failure_cannot_resume_or_publish_a_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            partial = _PartialBinaryWriter(
                writer.handle,
                max_write=5,
                fail_on_call=2,
            )
            writer.handle = partial
            writer.flush_characters = 1
            try:
                with self.assertRaisesRegex(
                    OSError, "injected Result stream failure"
                ):
                    writer.append({
                        "schemaVersion": 3,
                        "cycleId": "one",
                        "decisionTime": "2026-01-01T00:00:00Z",
                        "data": {"value": 1},
                    })

                self.assertTrue(writer._stream_failed)
                self.assertGreater(writer._result_byte_count, len(b'{"cycles":[\n'))
                self.assertEqual(writer.content_digest, "")
                self.assertFalse(writer.finished)
                self.assertFalse(writer.published)
                self.assertFalse(path.exists())
                self.assertFalse(
                    (writer.staging_directory / "result-manifest.json").exists()
                )
                with self.assertRaisesRegex(
                    RuntimeError, "after its byte stream failed"
                ):
                    writer.finish(
                        _metadata("one"),
                        _catalog("result", _metadata("one")),
                    )
            finally:
                writer.discard()

    def test_caller_cannot_supply_or_bypass_the_streamed_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            try:
                injected = _metadata()
                injected["contentDigest"] = "sha256:" + "0" * 64
                with self.assertRaisesRegex(ValueError, "unsupported field"):
                    writer.finish(injected, _catalog("result", injected))
                self.assertEqual(writer.content_digest, "")
                self.assertFalse(writer.finished)
                self.assertFalse(writer.published)

                # Even direct bytes written around the Engine-owned stream do
                # not get a manifest: the exact accepted byte count is bound
                # before the staging directory can be published.
                writer.handle.write(b"caller-owned-byte")
                metadata = _metadata()
                with self.assertRaisesRegex(
                    RuntimeError, "byte count changed outside"
                ):
                    writer.finish(metadata, _catalog("result", metadata))
                self.assertEqual(writer.content_digest, "")
                self.assertFalse(writer.finished)
                self.assertFalse(writer.published)
                self.assertFalse(path.exists())
                self.assertFalse(
                    (writer.staging_directory / "result-manifest.json").exists()
                )
            finally:
                writer.discard()

    def test_records_atomic_publish_when_durability_step_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            writer.append(
                {
                    "schemaVersion": 3,
                    "cycleId": "one",
                    "decisionTime": "2026-01-01T00:00:00Z",
                    "data": {},
                }
            )
            metadata = {
                "schemaVersion": 8,
                "dataKeys": {},
                "metrics": {"cycleCount": 1},
                "executionChain": {},
                "sampleFrameContract": {
                    "frameCount": 1,
                    "firstCycleId": "one",
                    "lastCycleId": "one",
                },
            }
            catalog = {
                "backtestId": "result",
                "pipelineId": "pipeline",
                "datasetId": "dataset",
                "name": "Result writer fixture",
                "runner": "engine.backtest",
                "createdAt": "2026-01-01T00:00:00Z",
                "completedAt": "2026-01-01T00:00:00Z",
                "request": {},
                "metrics": metadata["metrics"],
                "visualization": {},
            }
            real_publish = version_archive.publish_staging_directory

            def publish_then_raise(staging, destination, *, managed_root):
                real_publish(staging, destination, managed_root=managed_root)
                raise OSError("destination parent fsync failed")

            with mock.patch.object(
                version_archive,
                "publish_staging_directory",
                side_effect=publish_then_raise,
            ):
                with self.assertRaisesRegex(
                    result_writer.BacktestResultPublicationUncertain,
                    "without a durability acknowledgement",
                ) as raised:
                    writer.finish(metadata, catalog)

            self.assertFalse(writer.finished)
            self.assertTrue(writer.published)
            self.assertTrue(path.is_file())
            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertEqual(
                str(raised.exception.__cause__),
                "destination parent fsync failed",
            )
            writer.discard()
            self.assertTrue(path.is_file())

    def test_does_not_type_ordinary_publish_failures_as_uncertain(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            writer.append(
                {
                    "schemaVersion": 3,
                    "cycleId": "one",
                    "decisionTime": "2026-01-01T00:00:00Z",
                    "data": {},
                }
            )
            metadata = {
                "schemaVersion": 8,
                "dataKeys": {},
                "metrics": {"cycleCount": 1},
                "executionChain": {},
                "sampleFrameContract": {
                    "frameCount": 1,
                    "firstCycleId": "one",
                    "lastCycleId": "one",
                },
            }
            catalog = {
                "backtestId": "result",
                "pipelineId": "pipeline",
                "datasetId": "dataset",
                "name": "Result writer fixture",
                "runner": "engine.backtest",
                "createdAt": "2026-01-01T00:00:00Z",
                "completedAt": "2026-01-01T00:00:00Z",
                "request": {},
                "metrics": metadata["metrics"],
                "visualization": {},
            }
            primary = RuntimeError("publish rejected before rename")

            with (
                mock.patch.object(
                    version_archive,
                    "publish_staging_directory",
                    side_effect=primary,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                writer.finish(metadata, catalog)

            self.assertIs(raised.exception, primary)
            self.assertFalse(writer.published)
            self.assertFalse(path.is_file())
            writer.discard()

    def test_does_not_type_post_rename_application_failure_as_uncertain(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result" / "result.json"
            writer = result_writer.BacktestResultWriter(path)
            writer.append(
                {
                    "schemaVersion": 3,
                    "cycleId": "one",
                    "decisionTime": "2026-01-01T00:00:00Z",
                    "data": {},
                }
            )
            metadata = {
                "schemaVersion": 8,
                "dataKeys": {},
                "metrics": {"cycleCount": 1},
                "executionChain": {},
                "sampleFrameContract": {
                    "frameCount": 1,
                    "firstCycleId": "one",
                    "lastCycleId": "one",
                },
            }
            catalog = {
                "backtestId": "result",
                "pipelineId": "pipeline",
                "datasetId": "dataset",
                "name": "Result writer fixture",
                "runner": "engine.backtest",
                "createdAt": "2026-01-01T00:00:00Z",
                "completedAt": "2026-01-01T00:00:00Z",
                "request": {},
                "metrics": metadata["metrics"],
                "visualization": {},
            }
            primary = RuntimeError("application failure after rename")
            real_publish = version_archive.publish_staging_directory

            def publish_then_fail(staging, destination, *, managed_root):
                real_publish(
                    staging,
                    destination,
                    managed_root=managed_root,
                )
                raise primary

            with (
                mock.patch.object(
                    version_archive,
                    "publish_staging_directory",
                    side_effect=publish_then_fail,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                writer.finish(metadata, catalog)

            self.assertIs(raised.exception, primary)
            self.assertTrue(writer.published)
            writer.discard()
            self.assertTrue(path.is_file())

    def test_publication_uncertainty_rejects_non_os_errors(self):
        with self.assertRaisesRegex(TypeError, "OS durability error"):
            result_writer.BacktestResultPublicationUncertain(
                RuntimeError("application failure")
            )

    def test_process_control_publish_failures_remain_process_control_failures(self):
        for primary in (
            KeyboardInterrupt("publish interrupted"),
            SystemExit("publish terminated"),
        ):
            with self.subTest(error_type=type(primary).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "result" / "result.json"
                    writer = result_writer.BacktestResultWriter(path)
                    writer.append(
                        {
                            "schemaVersion": 3,
                            "cycleId": "one",
                            "decisionTime": "2026-01-01T00:00:00Z",
                            "data": {},
                        }
                    )
                    metadata = {
                        "schemaVersion": 8,
                        "dataKeys": {},
                        "metrics": {"cycleCount": 1},
                        "executionChain": {},
                        "sampleFrameContract": {
                            "frameCount": 1,
                            "firstCycleId": "one",
                            "lastCycleId": "one",
                        },
                    }
                    catalog = {
                        "backtestId": "result",
                        "pipelineId": "pipeline",
                        "datasetId": "dataset",
                        "name": "Result writer fixture",
                        "runner": "engine.backtest",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "completedAt": "2026-01-01T00:00:00Z",
                        "request": {},
                        "metrics": metadata["metrics"],
                        "visualization": {},
                    }
                    real_publish = version_archive.publish_staging_directory

                    def publish_then_fail(staging, destination, *, managed_root):
                        real_publish(
                            staging,
                            destination,
                            managed_root=managed_root,
                        )
                        raise primary

                    with (
                        mock.patch.object(
                            version_archive,
                            "publish_staging_directory",
                            side_effect=publish_then_fail,
                        ),
                        self.assertRaises(type(primary)) as raised,
                    ):
                        writer.finish(metadata, catalog)

                    self.assertIs(raised.exception, primary)
                    self.assertTrue(writer.published)
                    writer.discard()
                    self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
