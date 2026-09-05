"""Low-priority Engine preparation of verified Result frames."""

from __future__ import annotations

import copy
import heapq
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from engine.runtime.projection_worker import (
    projection_source_generation,
    write_result_projection_in_worker,
    write_sample_result_projection_in_worker,
)


_CONDITION = threading.Condition()
_QUEUE = []
_KEYS = set()
_SEQUENCE = 0
_THREAD = None
_STOPPING = False
_COMPLETED = OrderedDict()
_COMPLETED_MAX_ENTRIES = 64
_LAST_ERROR = None


def _key(kind, evidence):
    return kind, evidence.get("contentDigest")


def _run_task(kind, evidence):
    writer = (
        write_result_projection_in_worker
        if kind == "backtest"
        else write_sample_result_projection_in_worker
    )
    with tempfile.TemporaryDirectory(prefix="trade-projection-prewarm-") as root:
        writer(
            evidence,
            ["dataKeys"],
            [],
            {},
            Path(root) / "projection.json",
            projection_format="rows",
            window=None,
            priority="background",
        )


def _worker_loop():
    global _LAST_ERROR, _THREAD
    while True:
        with _CONDITION:
            while not _QUEUE and not _STOPPING:
                _CONDITION.wait()
            if _STOPPING and not _QUEUE:
                _THREAD = None
                return
            _priority, _sequence, kind, evidence = heapq.heappop(_QUEUE)
        key = _key(kind, evidence)
        succeeded = False
        try:
            _run_task(kind, evidence)
            succeeded = True
        except BaseException as error:
            # Preparation is optional acceleration.  The foreground request
            # remains authoritative and reports its own exact failure.
            _LAST_ERROR = f"{type(error).__name__}: {error}"
        finally:
            with _CONDITION:
                _KEYS.discard(key)
                if succeeded:
                    generation = projection_source_generation(kind, evidence)
                    if generation is not None:
                        _COMPLETED[key] = generation
                        _COMPLETED.move_to_end(key)
                        while len(_COMPLETED) > _COMPLETED_MAX_ENTRIES:
                            _COMPLETED.popitem(last=False)


def prepare_projection_source(kind, evidence, *, interactive=False):
    """Queue one deduplicated frame preparation without blocking the caller."""

    global _SEQUENCE, _THREAD, _STOPPING
    if kind not in {"backtest", "sample"} or not isinstance(evidence, dict):
        raise ValueError("Projection preparation identity is invalid.")
    key = _key(kind, evidence)
    if not isinstance(key[1], str) or not key[1]:
        raise ValueError("Projection preparation Result digest is invalid.")
    with _CONDITION:
        _STOPPING = False
        generation = projection_source_generation(kind, evidence)
        if generation is not None and _COMPLETED.get(key) == generation:
            _COMPLETED.move_to_end(key)
            return False
        if key in _KEYS:
            if interactive:
                for index, item in enumerate(_QUEUE):
                    if _key(item[2], item[3]) == key and item[0] != 0:
                        _QUEUE[index] = (0, item[1], item[2], item[3])
                        heapq.heapify(_QUEUE)
                        break
            _CONDITION.notify_all()
            return False
        _SEQUENCE += 1
        heapq.heappush(
            _QUEUE,
            (0 if interactive else 10, _SEQUENCE, kind, copy.deepcopy(evidence)),
        )
        _KEYS.add(key)
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(
                target=_worker_loop,
                name="trade-projection-prewarm",
                daemon=True,
            )
            _THREAD.start()
        _CONDITION.notify_all()
    return True


def projection_prewarm_status():
    with _CONDITION:
        return {
            "running": bool(_THREAD and _THREAD.is_alive()),
            "queued": len(_QUEUE),
            "tracked": len(_KEYS),
            "prepared": len(_COMPLETED),
            "lastError": _LAST_ERROR,
        }


def shutdown_projection_prewarm(*, wait=True):
    global _LAST_ERROR, _STOPPING
    with _CONDITION:
        _STOPPING = True
        _QUEUE.clear()
        _KEYS.clear()
        _COMPLETED.clear()
        _LAST_ERROR = None
        thread = _THREAD
        _CONDITION.notify_all()
    if wait and thread is not None and thread is not threading.current_thread():
        thread.join(timeout=10)
    return thread is not None


__all__ = (
    "prepare_projection_source",
    "projection_prewarm_status",
    "shutdown_projection_prewarm",
)
