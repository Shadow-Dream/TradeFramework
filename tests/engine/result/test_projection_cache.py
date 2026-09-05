"""Engine-wide exact Result projection cache and single-flight."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from engine.service import projection_cache


class ProjectionCacheTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory(prefix="trade-projection-cache-test-")
        root = Path(self.owner.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
        }
        self.identity = projection_cache.projection_identity(
            kind="sample",
            result_id="sha256:" + "1" * 64,
            result_content_digest="sha256:" + "2" * 64,
            paths=["cycles.data.price.close"],
            temporary_modules=[],
            module_definitions={},
            projection_format="columns-v2",
            window=None,
        )

    def tearDown(self):
        self.owner.cleanup()

    @staticmethod
    def _write(path, token="one"):
        Path(path).write_text(
            json.dumps({"projectionFormat": "columns-v2", "token": token}),
            encoding="utf-8",
        )

    def test_exact_bytes_persist_and_corruption_recomputes(self):
        calls = 0

        def build(path):
            nonlocal calls
            calls += 1
            self._write(path, f"build-{calls}")

        first_path = Path(self.owner.name) / "first.json"
        second_path = Path(self.owner.name) / "second.json"
        first = projection_cache.write_cached_projection(
            self.config, self.identity, first_path, build
        )
        second = projection_cache.write_cached_projection(
            self.config, self.identity, second_path, build
        )
        self.assertFalse(first["hit"])
        self.assertTrue(second["hit"])
        self.assertEqual(calls, 1)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

        cache_payload = next(
            (Path(self.config["controlRoot"]) / "result-projections").glob(
                "[0-9a-f]*.json"
            )
        )
        if cache_payload.name.endswith(".meta.json"):
            cache_payload = next(
                path for path in cache_payload.parent.glob("[0-9a-f]*.json")
                if not path.name.endswith(".meta.json")
            )
        cache_payload.write_text("{}", encoding="utf-8")
        third = projection_cache.write_cached_projection(
            self.config,
            self.identity,
            Path(self.owner.name) / "third.json",
            build,
        )
        self.assertFalse(third["hit"])
        self.assertEqual(calls, 2)

    def test_concurrent_identical_misses_are_single_flight(self):
        calls = 0
        calls_lock = threading.Lock()
        barrier = threading.Barrier(4)
        outputs = []
        failures = []

        def build(path):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.08)
            self._write(path)

        def run(index):
            try:
                barrier.wait(timeout=2)
                destination = Path(self.owner.name) / f"thread-{index}.json"
                result = projection_cache.write_cached_projection(
                    self.config, self.identity, destination, build
                )
                outputs.append((result, destination.read_bytes()))
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=run, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(calls, 1)
        self.assertEqual(len(outputs), 4)
        self.assertEqual(sum(not result["hit"] for result, _data in outputs), 1)
        self.assertEqual(len({data for _result, data in outputs}), 1)


if __name__ == "__main__":
    unittest.main()
