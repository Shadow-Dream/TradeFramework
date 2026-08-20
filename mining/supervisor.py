"""Small process supervisor for the independently executable worker."""

from __future__ import annotations

import multiprocessing
import threading
import time
from typing import Any

from .worker import worker_process_main


class MiningSupervisor:
    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        self._context = multiprocessing.get_context("spawn")
        self._stop_event = self._context.Event()
        self._process = None
        self._thread = None
        self._lock = threading.Lock()
        self._stopping = False
        self._restart_count = 0
        self._last_exit_code = None
        self._last_started_at = None
        raw_retry_seconds = config.get("miningStandbyRetrySeconds", 15.0)
        if type(raw_retry_seconds) not in {int, float}:
            raise ValueError("miningStandbyRetrySeconds must be a finite number.")
        retry_seconds = float(raw_retry_seconds)
        if not 0.01 <= retry_seconds <= 300.0:
            raise ValueError(
                "miningStandbyRetrySeconds must be between 0.01 and 300."
            )
        self._standby_retry_seconds = retry_seconds

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stopping = False
            self._stop_event.clear()
            self._spawn_locked()
            self._thread = threading.Thread(
                target=self._monitor, name="mining-supervisor", daemon=True
            )
            self._thread.start()

    def _spawn_locked(self) -> None:
        self._process = self._context.Process(
            target=worker_process_main,
            args=(self.config, self._stop_event),
            name="trade-mining-worker",
            daemon=True,
        )
        self._process.start()
        self._last_started_at = time.time()

    def _monitor(self) -> None:
        delay = 1.0
        while True:
            with self._lock:
                if self._stopping:
                    return
                process = self._process
            if process is None:
                return
            process.join(timeout=1.0)
            if process.is_alive():
                continue
            self._last_exit_code = process.exitcode
            if self._stopping or self._stop_event.is_set():
                return
            wait_seconds = self._standby_retry_seconds if process.exitcode == 73 else delay
            # Exit 73 means another worker owns the lock. Stay in low-frequency
            # standby so this Engine can take over after that worker disappears.
            if self._stop_event.wait(wait_seconds) or self._stopping:
                return
            with self._lock:
                if self._stopping or self._stop_event.is_set():
                    return
                self._restart_count += 1
                self._spawn_locked()
            if process.exitcode != 73:
                delay = min(30.0, delay * 2.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            return {
                "enabled": True,
                "running": bool(process and process.is_alive()),
                "pid": process.pid if process else None,
                "restartCount": self._restart_count,
                "lastExitCode": self._last_exit_code,
                "lastStartedAt": self._last_started_at,
                "standby": bool(process and not process.is_alive() and self._last_exit_code == 73),
            }

    def shutdown(self, timeout: float = 10.0) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("Mining shutdown timeout must be a positive number.")
        timeout = float(timeout)
        if timeout <= 0:
            raise ValueError("Mining shutdown timeout must be a positive number.")
        with self._lock:
            self._stopping = True
            self._stop_event.set()
            process = self._process
            monitor = self._thread
        if process:
            process.join(timeout=timeout)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3.0)
            if process.is_alive():
                raise RuntimeError("Mining worker process did not stop.")
        if monitor and monitor is not threading.current_thread() and monitor.is_alive():
            monitor.join(timeout=min(timeout, 3.0))
            if monitor.is_alive():
                raise RuntimeError("Mining supervisor monitor did not stop.")
        with self._lock:
            if self._process is process:
                self._process = None
            if self._thread is monitor:
                self._thread = None
