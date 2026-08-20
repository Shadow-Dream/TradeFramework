"""Single-writer durable mining worker."""

from __future__ import annotations

import fcntl
import json
import math
import os
import random
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from .http_client import RobustHttpClient
from .providers import enabled_provider_ids, get_provider
from .providers.base import (
    BlockedProviderError,
    RetryableProviderError,
    canonical_json,
    record_entries,
)
from .store import MiningStore


class WorkerAlreadyRunning(RuntimeError):
    pass


class SingleWriterLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self) -> "SingleWriterLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise WorkerAlreadyRunning(f"Mining writer lock is already held: {self.path}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class MiningWorker:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        worker_id: str | None = None,
        lease_seconds: float = 45.0,
        poll_seconds: float = 1.0,
        random_source: random.Random | None = None,
        store: MiningStore | None = None,
    ):
        self.config = dict(config)
        self.store = store or MiningStore(config)
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        if type(lease_seconds) not in {int, float} or not math.isfinite(
            float(lease_seconds)
        ) or float(lease_seconds) < 5:
            raise ValueError("Mining worker leaseSeconds must be finite and at least 5.")
        if type(poll_seconds) not in {int, float} or not math.isfinite(
            float(poll_seconds)
        ) or float(poll_seconds) <= 0:
            raise ValueError("Mining worker pollSeconds must be a positive finite number.")
        self.lease_seconds = float(lease_seconds)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.random = random_source or random.Random()
        self.allowed_providers = enabled_provider_ids(
            include_test=self.config.get("miningExposeTestProvider") is True
        )

    def run(self, stop_event: Any = None, *, once: bool = False) -> int:
        with SingleWriterLock(self.store.root / "worker.lock"):
            self.store.recover_orphans()
            self.store.recover_expired_leases()
            self._worker_heartbeat("idle")
            completed = 0
            while not (stop_event and stop_event.is_set()):
                self.store.recover_expired_leases()
                job = self.store.claim_next(
                    self.worker_id,
                    self.lease_seconds,
                    allowed_providers=self.allowed_providers,
                )
                if not job:
                    self._worker_heartbeat("idle")
                    if once:
                        break
                    if stop_event:
                        stop_event.wait(self.poll_seconds)
                    else:
                        time.sleep(self.poll_seconds)
                    continue
                self._worker_heartbeat("working", job["jobId"])
                try:
                    self._process_job(job, stop_event)
                except Exception as exc:
                    # Setup/registry failures happen before the inner page loop.  Keep
                    # their evidence durable instead of letting the process restart
                    # forever with an owned Lease.
                    self.store.retry_job(
                        job["jobId"], self.worker_id, str(exc), jitter=self.random.uniform(0.0, 1.0)
                    )
                    self._worker_heartbeat("retry_wait", job["jobId"], str(exc))
                completed += 1
                self._worker_heartbeat("idle")
                if once:
                    break
            self._worker_heartbeat("stopped")
            return completed

    def _worker_heartbeat(
        self, status: str, job_id: str | None = None, error: str | None = None
    ) -> None:
        self.store.worker_heartbeat(
            self.worker_id,
            pid=os.getpid(),
            status=status,
            current_job_id=job_id,
            error=error,
        )

    def _wait_for_rate_slot(
        self, job_id: str, delay: float, stop_event: Any = None
    ) -> bool:
        """Wait without allowing the owned Lease to expire; return false on stop."""
        deadline = time.monotonic() + max(0.0, delay)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            chunk = min(remaining, max(1.0, self.lease_seconds / 3.0))
            if stop_event:
                if stop_event.wait(chunk):
                    return False
            else:
                time.sleep(chunk)
            if not self.store.heartbeat(job_id, self.worker_id, self.lease_seconds):
                return False

    def _process_job(self, job: dict[str, Any], stop_event: Any = None) -> None:
        job_id = job["jobId"]
        provider_class = get_provider(job["provider"])
        provider = provider_class()
        provider_config = dict(job["providerConfig"])
        provider_config["_runtimeAttempt"] = job["attemptCount"]
        refill = job.get("refill")
        lane = "refill" if refill else "main"
        refill_id = refill.get("refill_id") if refill else None
        if refill:
            cursor = refill["cursor"]
        else:
            checkpoint = job.get("cursor")
            if checkpoint is None:
                checkpoint = provider_class.initial_cursor(provider_config)
            cursor = provider_class.overlap_cursor(
                checkpoint, int(job.get("overlapRecords") or 0), provider_config
            )
        self.store.begin_active(
            job_id,
            self.worker_id,
            lane=lane,
            cursor=cursor,
            refill_id=refill_id,
            lease_seconds=self.lease_seconds,
        )
        seen_pages: set[str] = set()
        pages_processed = 0
        max_pages = max(1, int(self.config.get("miningMaxPagesPerRun", 25)))
        rate_key = provider_class.rate_limit_key(provider_config)
        minimum_interval = provider_class.minimum_request_interval(provider_config)

        def counted_request() -> None:
            self.store.metric("provider_requests", 1)

        with RobustHttpClient(
            timeout=float(self.config.get("miningHttpTimeout", 20.0)),
            on_request=counted_request,
        ) as client:
            while True:
                if stop_event and stop_event.is_set():
                    self.store.release_job(job_id, self.worker_id)
                    return
                if self.store.pause_requested(job_id, self.worker_id):
                    self.store.release_job(job_id, self.worker_id)
                    return
                try:
                    rate_delay = self.store.reserve_rate_slot(rate_key, minimum_interval)
                    if rate_delay and not self._wait_for_rate_slot(job_id, rate_delay, stop_event):
                        self.store.release_job(job_id, self.worker_id)
                        return
                    self.store.transition(
                        job_id, self.worker_id, "fetching", self.lease_seconds
                    )
                    page = provider.fetch_page(cursor, provider_config, client)
                    if len(page.raw) > int(self.config.get("miningMaxPageBytes", 64 * 1024 * 1024)):
                        raise BlockedProviderError("Provider page exceeds miningMaxPageBytes.")
                    provider_class.validate_page(page, provider_config)
                    next_cursor = provider_class.next_cursor(cursor, page, provider_config)
                    continue_fetch = provider_class.should_continue(page, next_cursor, provider_config)
                    if next_cursor is not None and canonical_json(next_cursor) == canonical_json(cursor):
                        raise BlockedProviderError(
                            "Provider pagination made no progress: next cursor equals request cursor."
                        )
                    page_fingerprint = canonical_json({
                        "raw": __import__("hashlib").sha256(page.raw).hexdigest(),
                        "next": next_cursor,
                    })
                    if next_cursor is not None and page_fingerprint in seen_pages:
                        raise BlockedProviderError(
                            "Provider pagination repeated the same page and next cursor."
                        )
                    seen_pages.add(page_fingerprint)
                    entries = record_entries(provider_class, page.records, provider_config)
                    self.store.transition(
                        job_id, self.worker_id, "committing", self.lease_seconds
                    )
                    committed = self.store.commit_page(
                        job_id=job_id,
                        owner=self.worker_id,
                        lane=lane,
                        refill_id=refill_id,
                        request_cursor=cursor,
                        next_cursor=next_cursor,
                        raw=page.raw,
                        response_status=page.status_code,
                        response_headers=page.headers,
                        source=page.source,
                        entries=entries,
                        continue_fetch=continue_fetch,
                        lease_seconds=self.lease_seconds,
                    )
                except BlockedProviderError as exc:
                    self.store.block_job(job_id, self.worker_id, str(exc))
                    self._worker_heartbeat("blocked", job_id, str(exc))
                    return
                except ValueError as exc:
                    self.store.block_job(job_id, self.worker_id, str(exc))
                    self._worker_heartbeat("blocked", job_id, str(exc))
                    return
                except RetryableProviderError as exc:
                    jitter = self.random.uniform(0.0, 1.0)
                    self.store.retry_job(
                        job_id,
                        self.worker_id,
                        str(exc),
                        retry_after=exc.retry_after,
                        jitter=jitter,
                    )
                    self._worker_heartbeat("retry_wait", job_id, str(exc))
                    return
                except Exception as exc:
                    jitter = self.random.uniform(0.0, 1.0)
                    self.store.retry_job(job_id, self.worker_id, str(exc), jitter=jitter)
                    self._worker_heartbeat("retry_wait", job_id, str(exc))
                    return
                if committed["status"] != "leased" or next_cursor is None:
                    return
                pages_processed += 1
                if pages_processed >= max_pages:
                    # The cursor/refill cursor was committed with the page. Releasing
                    # here gives other due jobs a chance without losing one record.
                    self.store.release_job(job_id, self.worker_id)
                    return
                cursor = next_cursor


def worker_process_main(config: dict[str, Any], stop_event: Any) -> None:
    try:
        MiningWorker(config).run(stop_event)
    except WorkerAlreadyRunning:
        raise SystemExit(73)
