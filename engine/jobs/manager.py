"""Bounded orchestration of persistent Backtest jobs."""

from __future__ import annotations

import copy
import logging
import shutil
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from engine.contracts import strict_json
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.jobs.repository import BacktestJobRepository
from engine.worker.backtest_supervisor import (
    run_backtest_runtime,
    runtime_process_authority,
    shutdown_backtest_runtimes,
)


LOGGER = logging.getLogger(__name__)
_PREPARED_SUBMISSION_OMITTED = object()
_DURABLE_PROGRESS_INTERVAL_SECONDS = 1.0
_QUEUED_DISPATCH_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class BacktestJobServices:
    """High-level Backtest capabilities supplied by the service composition root."""

    freeze_request: object
    reconcile_result_staging: object
    recover_result_catalog: object
    validate_result_archive: object
    consume_prepared_request: object = None
    validate_frozen_admission: object = None

    def __post_init__(self):
        for field in (
            "freeze_request",
            "reconcile_result_staging",
            "recover_result_catalog",
            "validate_result_archive",
        ):
            if not callable(getattr(self, field)):
                raise TypeError(f"Backtest Job service '{field}' must be callable.")
        for field in (
            "consume_prepared_request",
            "validate_frozen_admission",
        ):
            capability = getattr(self, field)
            if capability is not None and not callable(capability):
                raise TypeError(
                    f"Backtest Job service '{field}' must be callable."
                )


class BacktestJobManager:
    """Own the Engine's bounded pool of independent Backtest runs."""

    def __init__(
        self,
        config,
        services,
        *,
        max_workers=None,
        event_callback=None,
        runtime_launcher=None,
        repository=None,
    ):
        if not isinstance(services, BacktestJobServices):
            raise TypeError("Backtest Job services are required.")
        self.config = copy.deepcopy(config)
        self.services = services
        default_workers = 2
        selected_workers = (
            max_workers
            if max_workers is not None
            else self.config["backtestMaxWorkers"]
            if "backtestMaxWorkers" in self.config
            else default_workers
        )
        if (
            isinstance(selected_workers, bool)
            or not isinstance(selected_workers, int)
            or selected_workers < 1
        ):
            raise ValueError("Backtest max workers must be a positive integer.")
        self.max_workers = selected_workers
        self.event_callback = event_callback
        if runtime_launcher is None:
            def launch_default_runtime(
                runtime_config,
                request,
                *,
                backtest_id,
                progress_callback,
                execution_root,
                should_stop,
            ):
                return run_backtest_runtime(
                    runtime_config,
                    request,
                    backtest_id=backtest_id,
                    progress_callback=progress_callback,
                    execution_root=execution_root,
                    should_stop=should_stop,
                )

            self.runtime_launcher = launch_default_runtime
        else:
            self.runtime_launcher = runtime_launcher
        if not callable(self.runtime_launcher):
            raise TypeError("Backtest runtime launcher must be callable.")
        self.repository = (
            BacktestJobRepository(self.config)
            if repository is None
            else repository
        )
        self._stopping = threading.Event()
        self._retained_execution_roots = {}
        self._retained_execution_roots_lock = threading.Lock()
        self._dispatch_lock = threading.RLock()
        self._dispatch_futures = {}
        self._dispatch_timers = {}
        self._dispatch_recoveries = {}
        self._retired_executors = []
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="backtest-job",
        )
        self.repository.prepare()
        self.services.reconcile_result_staging(self.config)
        self._fail_interrupted_jobs()

    def _new_executor(self):
        return ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="backtest-job",
        )

    def _track_execution(self, job_id, backtest_id, request):
        with self._dispatch_lock:
            future = self._executor.submit(
                self._execute,
                job_id,
                backtest_id,
                copy.deepcopy(request),
            )
            timer = threading.Timer(
                _QUEUED_DISPATCH_TIMEOUT_SECONDS,
                self._recover_starved_dispatch,
                args=(job_id, backtest_id, copy.deepcopy(request), future),
            )
            timer.daemon = True
            self._dispatch_futures[job_id] = future
            self._dispatch_timers[job_id] = timer
            future.add_done_callback(
                lambda completed, identity=job_id: self._execution_finished(
                    identity,
                    completed,
                )
            )
            if self._dispatch_futures.get(job_id) is future:
                timer.start()
            return future

    def _execution_finished(self, job_id, future):
        with self._dispatch_lock:
            if self._dispatch_futures.get(job_id) is future:
                self._dispatch_futures.pop(job_id, None)
                timer = self._dispatch_timers.pop(job_id, None)
                self._dispatch_recoveries.pop(job_id, None)
            else:
                timer = None
        if timer is not None:
            timer.cancel()
        try:
            exception = future.exception()
        except CancelledError:
            return
        if exception is None:
            return
        try:
            self._fail_job(
                job_id,
                "Backtest dispatcher failed before terminalizing the Job: "
                + (str(exception) or exception.__class__.__name__),
            )
        except BaseException:
            LOGGER.exception(
                "Backtest dispatcher could not terminalize Job %s",
                job_id,
            )

    def _reschedule_dispatch_watchdog(
        self,
        job_id,
        backtest_id,
        request,
        future,
    ):
        timer = threading.Timer(
            _QUEUED_DISPATCH_TIMEOUT_SECONDS,
            self._recover_starved_dispatch,
            args=(job_id, backtest_id, copy.deepcopy(request), future),
        )
        timer.daemon = True
        with self._dispatch_lock:
            if (
                self._stopping.is_set()
                or self._dispatch_futures.get(job_id) is not future
                or future.done()
            ):
                return
            self._dispatch_timers[job_id] = timer
        timer.start()

    def _recover_starved_dispatch(
        self,
        job_id,
        backtest_id,
        request,
        future,
    ):
        with self._dispatch_lock:
            if (
                self._stopping.is_set()
                or self._dispatch_futures.get(job_id) is not future
                or future.done()
            ):
                return
            other_running = sum(
                candidate.running()
                for identity, candidate in self._dispatch_futures.items()
                if identity != job_id
            )
        running_jobs = sum(
            status == "running"
            for _job_id, _backtest_id, status in self.repository.active_references()
        )
        if future.running() or max(other_running, running_jobs) >= self.max_workers:
            self._reschedule_dispatch_watchdog(
                job_id,
                backtest_id,
                request,
                future,
            )
            return
        with self._dispatch_lock:
            if (
                self._stopping.is_set()
                or self._dispatch_futures.get(job_id) is not future
            ):
                return
            recovery_exhausted = self._dispatch_recoveries.get(job_id, 0) >= 1
            if not future.cancel():
                return
            if not recovery_exhausted:
                old_executor = self._executor
                old_executor.shutdown(wait=False, cancel_futures=True)
                self._retired_executors.append(old_executor)
                self._executor = self._new_executor()
                self._dispatch_recoveries[job_id] = 1
        if recovery_exhausted:
            self._fail_job(
                job_id,
                "Backtest dispatcher remained queued after one recovery attempt.",
            )
            return
        self._emit(
            "backtest.job.dispatch.recovered",
            {
                **self.repository.get(job_id),
                "reason": "queued Job had no running executor capacity",
            },
        )
        self._track_execution(job_id, backtest_id, request)

    def _completed_evidence(self, job_id, backtest_id):
        expected_request = self.repository.active_request_for_job(
            job_id,
            backtest_id,
        )
        if expected_request is None:
            return None
        row = self.repository.result_catalog_row(backtest_id)
        recovered = None
        if row is None:
            recovered = self.services.recover_result_catalog(
                self.config,
                backtest_id,
                expected_request,
            )
            if recovered is None:
                return None
            row = self.repository.result_catalog_row(backtest_id)
            if row is None:
                raise RuntimeError(
                    "Recovered Backtest Result has no catalog row."
                )
        else:
            # An existing catalog is completion evidence only for the exact
            # active Job request.  The recovery service validates that durable
            # request binding before the ordinary streamed archive validation.
            self.services.recover_result_catalog(
                self.config,
                backtest_id,
                expected_request,
            )
        if row["status"] not in {"completed", "archived"} or not row[
            "completed_at"
        ]:
            raise ValueError("Recovered Backtest catalog state is invalid.")
        # A catalog recovered in this call has already passed the complete
        # streamed Result validation.  Existing durable catalogs still take
        # the ordinary validation path during restart reconciliation.
        validation = (
            {"metrics": recovered["metrics"]}
            if recovered is not None
            else self.services.validate_result_archive(
                self.config,
                backtest_id,
            )
        )
        try:
            catalog_metrics = strict_json.loads(row["metrics_json"])
        except ValueError as exc:
            raise ValueError("Recovered Backtest metrics are invalid.") from exc
        if not isinstance(catalog_metrics, dict):
            raise ValueError("Recovered Backtest metrics are invalid.")
        cycle_count = validation["metrics"]["cycleCount"]
        if catalog_metrics.get("cycleCount") != cycle_count:
            raise ValueError(
                "Recovered Backtest metrics do not match its sealed Result."
            )
        if (
            isinstance(cycle_count, bool)
            or not isinstance(cycle_count, int)
            or cycle_count < 0
        ):
            raise ValueError("Recovered Backtest cycleCount is invalid.")
        return row["completed_at"], cycle_count

    def _fail_interrupted_jobs(self):
        completed = []
        for job_id, backtest_id, status in self.repository.active_references():
            if status != "running":
                continue
            evidence = self._completed_evidence(job_id, backtest_id)
            if evidence is not None:
                completed.append((job_id, *evidence))
        for job_id, completed_at, cycle_count in completed:
            self.repository.mark_completed(job_id, completed_at, cycle_count)
        self.repository.interrupt_active(engine_clock.utc_now())

    def submit(
        self,
        request,
        *,
        prepared_submission_token=_PREPARED_SUBMISSION_OMITTED,
        session_identity=None,
    ):
        if self._stopping.is_set():
            raise RuntimeError("Backtest job service is stopping.")
        if not isinstance(request, dict):
            raise ValueError("Backtest request must be an object.")
        prepared = prepared_submission_token is not _PREPARED_SUBMISSION_OMITTED
        if not prepared:
            request = self.services.freeze_request(
                self.config,
                copy.deepcopy(request),
            )
        else:
            if (
                not isinstance(prepared_submission_token, str)
                or not prepared_submission_token
            ):
                raise ValueError(
                    "Prepared Backtest submission token must be a non-empty string."
                )
            if not isinstance(session_identity, str) or not session_identity:
                raise ValueError(
                    "Prepared Backtest submission session identity is required."
                )
            if self.services.consume_prepared_request is None:
                raise RuntimeError(
                    "Prepared Backtest submissions are not configured."
                )
            if self.services.validate_frozen_admission is None:
                raise RuntimeError(
                    "Prepared Backtest admission validation is not configured."
                )
            request = self.services.consume_prepared_request(
                prepared_submission_token,
                copy.deepcopy(request),
                session_identity=session_identity,
            )
            request = self.services.validate_frozen_admission(
                self.config,
                request,
            )
        pipeline_id = request["pipeline"]["pipelineId"]
        dataset_id = request["datasetId"]
        if not isinstance(pipeline_id, str) or not pipeline_id:
            raise ValueError("pipeline.pipelineId must be a non-empty string.")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError("datasetId must be a non-empty string.")
        job_id = resource_ids.new_resource_id("job")
        backtest_id = resource_ids.new_resource_id("backtest")
        try:
            self.repository.insert_queued(
                job_id=job_id,
                backtest_id=backtest_id,
                pipeline_id=pipeline_id,
                dataset_id=dataset_id,
                request=request,
                submitted_at=engine_clock.utc_now(),
                snapshot_hash=request["executionSnapshot"]["snapshotHash"],
            )
        except BaseException as exc:
            if prepared:
                failure = RuntimeError(
                    "Prepared Backtest submission was consumed before the Job "
                    "could be durably queued."
                )
                raise failure from exc
            raise
        job = self.repository.get(job_id)
        self._emit("backtest.job.submitted", job)
        try:
            self._track_execution(job_id, backtest_id, request)
        except BaseException as exc:
            self._fail_job(job_id, str(exc) or exc.__class__.__name__)
            raise
        return job

    def _execute(self, job_id, backtest_id, request):
        execution_root = (
            Path(self.config["controlRoot"]) / "backtest-runs" / job_id
        )
        try:
            if self._stopping.is_set():
                raise RuntimeError(
                    "Engine stopped before this Backtest job started."
                )
            self.repository.mark_running(job_id, engine_clock.utc_now())
            self._emit("backtest.job.started", self.repository.get(job_id))
            execution_root.mkdir(parents=True, exist_ok=True)
            last_write = {"at": 0.0, "completed": -1, "phase": ""}

            def progress(completed_cycles, total_cycles, phase="running"):
                if self._stopping.is_set():
                    raise RuntimeError(
                        "Engine stopped while this Backtest job was running."
                    )
                if (
                    isinstance(completed_cycles, bool)
                    or not isinstance(completed_cycles, int)
                    or completed_cycles < 0
                    or isinstance(total_cycles, bool)
                    or not isinstance(total_cycles, int)
                    or total_cycles < 0
                    or (
                        total_cycles > 0
                        and completed_cycles > total_cycles
                    )
                    or not isinstance(phase, str)
                    or not phase
                ):
                    raise ValueError("Backtest progress update is invalid.")
                now = time.monotonic()
                force = (
                    phase != last_write["phase"]
                    or completed_cycles >= total_cycles > 0
                    or now - last_write["at"]
                    >= _DURABLE_PROGRESS_INTERVAL_SECONDS
                )
                if not force or (
                    completed_cycles == last_write["completed"]
                    and phase == last_write["phase"]
                ):
                    return
                self.repository.record_progress(
                    job_id,
                    phase=phase,
                    total_cycles=total_cycles,
                    completed_cycles=completed_cycles,
                )
                last_write.update(
                    at=now,
                    completed=completed_cycles,
                    phase=phase,
                )

            def should_stop():
                return self._stopping.is_set()

            should_stop.event = self._stopping
            self.runtime_launcher(
                self.config,
                request,
                backtest_id=backtest_id,
                progress_callback=progress,
                execution_root=execution_root,
                should_stop=should_stop,
            )
            evidence = self._completed_evidence(job_id, backtest_id)
            if evidence is None:
                raise RuntimeError(
                    "Backtest Runtime returned without committing its sealed Result."
                )
            self.repository.mark_completed(job_id, *evidence)
            self._emit("backtest.job.completed", self.repository.get(job_id))
        except BaseException as exc:
            if runtime_process_authority(execution_root):
                # The exact child authority remains registered for shutdown
                # retry.  Its Job and scratch cannot become terminal while it
                # may still write Result or runtime files.
                return
            verification_error = None
            try:
                evidence = self._completed_evidence(job_id, backtest_id)
            except BaseException as secondary_error:
                verification_error = secondary_error
                evidence = None
            if evidence is not None:
                self.repository.mark_completed(job_id, *evidence)
                self._emit(
                    "backtest.job.completed",
                    self.repository.get(job_id),
                )
                return
            phase = "interrupted" if self._stopping.is_set() else "failed"
            error = str(exc) or exc.__class__.__name__
            if verification_error is not None:
                verification_message = (
                    str(verification_error)
                    or verification_error.__class__.__name__
                )
                if verification_message != error:
                    error = (
                        "Backtest Result completion verification failed: "
                        f"{verification_message}; Runtime failure: {error}"
                    )
            self._fail_job(
                job_id,
                error,
                phase,
            )
        finally:
            if runtime_process_authority(execution_root):
                with self._retained_execution_roots_lock:
                    self._retained_execution_roots[execution_root] = job_id
            else:
                shutil.rmtree(execution_root, ignore_errors=True)

    def _fail_job(self, job_id, error, phase="failed"):
        changed = self.repository.fail_active(
            job_id,
            phase=phase,
            completed_at=engine_clock.utc_now(),
            error=error,
        )
        if changed:
            self._emit("backtest.job.failed", self.repository.get(job_id))
        return changed

    def _emit(self, event_type, payload):
        if self.event_callback is None:
            return
        try:
            self.event_callback(event_type, payload)
        except Exception:
            LOGGER.exception("Backtest job event callback failed: %s", event_type)

    def list(self, limit=50):
        return self.repository.list(limit)

    def get(self, job_id):
        return self.repository.get(job_id)

    def shutdown(self):
        self._stopping.set()
        with self._dispatch_lock:
            timers = tuple(self._dispatch_timers.values())
            self._dispatch_timers.clear()
        for timer in timers:
            timer.cancel()
        first_error = None
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except BaseException as exc:
            first_error = exc
            try:
                self._executor.shutdown(wait=True, cancel_futures=True)
            except BaseException as retry_error:
                first_error = first_error or retry_error
        for executor in self._retired_executors:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except BaseException as exc:
                first_error = first_error or exc
        execution_parent = Path(self.config["controlRoot"]) / "backtest-runs"
        try:
            shutdown_backtest_runtimes(execution_parent)
        except BaseException as exc:
            first_error = first_error or exc
        with self._retained_execution_roots_lock:
            retained = tuple(self._retained_execution_roots.items())
        now = engine_clock.utc_now()
        for execution_root, job_id in retained:
            if runtime_process_authority(execution_root):
                continue
            terminalized = False
            try:
                try:
                    backtest_id = self.repository.get(job_id)["backtestId"]
                    evidence = self._completed_evidence(job_id, backtest_id)
                except BaseException as exc:
                    first_error = first_error or exc
                    evidence = None
                if evidence is not None:
                    self.repository.mark_completed(job_id, *evidence)
                    self._emit(
                        "backtest.job.completed",
                        self.repository.get(job_id),
                    )
                else:
                    self._fail_job(
                        job_id,
                        "Engine stopped before this Backtest job completed.",
                        "interrupted",
                    )
                terminalized = True
            except BaseException as exc:
                first_error = first_error or exc
            finally:
                shutil.rmtree(execution_root, ignore_errors=True)
            if terminalized:
                with self._retained_execution_roots_lock:
                    self._retained_execution_roots.pop(execution_root, None)
        self.repository.interrupt_queued(now)
        if first_error is not None:
            raise first_error
